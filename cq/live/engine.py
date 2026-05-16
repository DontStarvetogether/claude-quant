"""
LiveEngine：实盘引擎。

与 BacktestEngine 共享完全相同的策略接口（Strategy ABC），
将数据源替换为 QMT 实时行情，执行层替换为 QMT 券商 API。

支持两种运行模式：
  run()          实盘模式  — 使用 QMTRealtimeFeed + QMTExecutor，需要 QMT 环境
  paper_trade()  纸上交易  — 使用 SimulatedExecutor + BarMatchingEngine（D+1 撮合）

每日运行流程：
  9:25  同步券商持仓（sync_positions）
  9:30  strategy.before_trading(today)
  9:30+ 实时 BarEvent → strategy.on_bar() → SignalEvent → 执行器下单/模拟成交
        成交回调 → FillEvent → portfolio.on_fill（持仓记账）
  15:00 strategy.after_trading(today)
        portfolio.settle_eod（T+1 本地解锁）

线程模型：
  主线程     ── 运行事件循环，处理所有 EventBus 事件（单线程，无锁竞争）
  Feed 线程  ── 运行行情接收，推送 Bar 到 bar_queue（queue.Queue，线程安全）
  QMT网络线程 ── 推送成交回报到 executor.event_queue（queue.Queue，线程安全）
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional, Protocol, runtime_checkable

from loguru import logger

from cq.core.event_bus import EventBus
from cq.core.events import BarEvent, EndOfDayEvent, FillEvent, OrderEvent, RejectEvent, SignalEvent
from cq.core.models import Bar
from cq.data.calendar import TradingCalendar
from cq.data.feed.historical import HistoricalFeed
from cq.data.feed.realtime import RealtimeFeed
from cq.data.store.parquet_store import ParquetStore
from cq.engine.portfolio import PortfolioManager
from cq.risk.pre_trade import PreTradeRisk
from cq.strategy.base import Strategy, StrategyContext
from cq.utils.config import Config

# 关键时间节点（hour, minute）
_TIME_PRE_SYNC = (9, 25)   # 同步持仓
_TIME_OPEN = (9, 30)        # 开盘，触发 before_trading
_TIME_CLOSE = (15, 0)       # 收盘，触发 after_trading + EOD 结算


@runtime_checkable
class _Executor(Protocol):
    """执行器协议（QMTExecutor 满足此接口，用于实盘模式）。"""
    event_queue: queue.Queue

    def connect(self) -> None: ...
    def sync_positions(self) -> None: ...
    def set_current_date(self, trade_date: date) -> None: ...
    def on_signal(self, event: SignalEvent) -> None: ...


class LiveEngine:
    """
    实盘引擎。

    实盘用法：
        config = Config.from_yaml("config/default.yaml")
        config.live.account_id = "你的资金账号"
        config.live.mini_qmt_dir = "C:/QMT/userdata_mini"

        engine = LiveEngine(config)
        engine.add_strategy(MyStrategy(), symbols=["600519.SH"])
        engine.run()

    纸上交易用法（D+1 撮合，与回测逻辑一致）：
        store = ParquetStore(config.data.root_path)
        engine.paper_trade(
            store=store,
            start_date=date(2024, 6, 3),
            end_date=date(2024, 6, 28),
        )
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._strategy: Optional[Strategy] = None
        self._symbols: list[str] = []
        self._stop_event = threading.Event()
        # 供外部（如 Web 层）读取实时状态
        self._portfolio: Optional[PortfolioManager] = None
        self._bus: Optional[EventBus] = None

    def add_strategy(self, strategy: Strategy, symbols: list[str]) -> None:
        if not strategy.strategy_id:
            raise ValueError("strategy.strategy_id 不能为空")
        self._strategy = strategy
        self._symbols = list(symbols)

    # ── 运行模式 ──────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """
        实盘模式：使用 QMTRealtimeFeed + QMTExecutor。
        需要 QMT 客户端已启动，config.live 已配置 account_id / mini_qmt_dir。
        """
        live_cfg = self._config.live
        if not live_cfg.account_id:
            raise ValueError("config.live.account_id 不能为空")

        from cq.data.feed.realtime import QMTRealtimeFeed
        from cq.execution.qmt import QMTExecutor

        store = ParquetStore(self._config.data.root_path)
        today = date.today()
        history_start = today - timedelta(days=live_cfg.history_days)

        bus, portfolio, risk, ctx = self._build_components(store, history_start, today)
        self._bus, self._portfolio = bus, portfolio

        executor = QMTExecutor(
            bus=bus,
            portfolio=portfolio,
            risk=risk,
            account_id=live_cfg.account_id,
            mini_qmt_dir=live_cfg.mini_qmt_dir,
            session_id=live_cfg.session_id,
        )
        feed = QMTRealtimeFeed(
            data_dir=live_cfg.mini_qmt_dir,
            period=live_cfg.bar_period,
        )

        logger.info(
            f"实盘模式启动  策略={self._strategy.strategy_id}  "
            f"账户={live_cfg.account_id}  标的={self._symbols}"
        )
        self._run_event_loop(bus, portfolio, executor, feed, ctx, today)

    def paper_trade(
        self,
        store: ParquetStore,
        start_date: date,
        end_date: date,
    ) -> None:
        """
        纸上交易模式：使用 SimulatedExecutor + BarMatchingEngine。
        与 BacktestEngine 共享完全相同的 D+1 撮合逻辑，
        但通过 LiveEngine 的事件循环运行（支持 SSE 实时推送、DB 持久化）。

        参数：
            store       ParquetStore 实例
            start_date  回放起始日期
            end_date    回放结束日期（含）
        """
        from cq.engine.matching.bar_matching import BarMatchingEngine
        from cq.execution.simulated import SimulatedExecutor

        history_start = start_date - timedelta(days=self._config.live.history_days)
        bus, portfolio, risk, ctx = self._build_components(store, history_start, end_date)
        self._bus, self._portfolio = bus, portfolio

        calendar = self._load_calendar(store)
        executor = SimulatedExecutor(bus=bus, portfolio=portfolio, risk=risk)
        matching = BarMatchingEngine(bus, portfolio, calendar, self._config.engine)

        logger.info(
            f"纸上交易模式（D+1 撮合）  策略={self._strategy.strategy_id}  "
            f"{start_date}→{end_date}  标的={self._symbols}"
        )
        self._run_paper_loop(bus, portfolio, executor, matching, ctx, start_date)

    def stop(self) -> None:
        """线程安全地停止引擎。"""
        logger.info("收到停止信号")
        self._stop_event.set()

    # ── 内部事件循环 ───────────────────────────────────────────────────────────────

    def _run_event_loop(
        self,
        bus: EventBus,
        portfolio: PortfolioManager,
        executor: _Executor,
        feed: RealtimeFeed,
        ctx: StrategyContext,
        trade_date: date,
    ) -> None:
        """实盘事件循环（按系统时钟驱动，阻塞运行）。"""
        self._register_handlers(bus, portfolio, executor, self._strategy)
        executor.connect()
        self._strategy._setup(bus, ctx)
        self._strategy.on_init()

        bar_queue: queue.Queue[Bar] = queue.Queue()
        feed.set_bar_callback(bar_queue.put)
        feed.subscribe(self._symbols)

        feed_thread = threading.Thread(target=feed.start, daemon=True, name="qmt-feed")
        feed_thread.start()

        synced = before_done = after_done = False

        try:
            while not self._stop_event.is_set():
                t = (datetime.now().hour, datetime.now().minute)

                if t >= _TIME_PRE_SYNC and not synced:
                    executor.set_current_date(trade_date)
                    ctx._set_date(trade_date)
                    executor.sync_positions()
                    synced = True

                if t >= _TIME_OPEN and not before_done:
                    self._strategy.before_trading(trade_date)
                    before_done = True

                self._drain_executor_events(executor.event_queue, bus)
                self._drain_bar_queue(bar_queue, bus, portfolio)
                bus.dispatch_all()

                if t >= _TIME_CLOSE and not after_done:
                    self._strategy.after_trading(trade_date)
                    bus.put(EndOfDayEvent(trade_date=trade_date))
                    bus.dispatch_all()
                    after_done = True
                    logger.info(
                        f"收盘结算完成  总资产 {portfolio.get_total_assets():,.0f} 元"
                    )

                time.sleep(0.5)

        except KeyboardInterrupt:
            logger.info("收到 Ctrl-C，正在退出…")
        finally:
            feed.stop()

    def _run_paper_loop(
        self,
        bus: EventBus,
        portfolio: PortfolioManager,
        executor,
        matching,
        ctx: StrategyContext,
        start_date: date,
    ) -> None:
        """
        纸上交易事件循环（D+1 撮合，与 BacktestEngine 完全对齐）。

        每个交易日：
          1. on_bar() → 更新撮合引擎 Bar 缓存
          2. matching.process_pending_orders() → 撮合前日订单（D+1）
          3. portfolio.update_prices() → 更新持仓市值
          4. before_trading → 推送 BarEvent → SignalEvent → OrderEvent
          5. EndOfDayEvent → settle_eod（T+1 解锁）
          6. after_trading
        """
        self._register_handlers(bus, portfolio, executor, self._strategy)
        bus.subscribe(OrderEvent, matching.on_order)
        self._strategy._setup(bus, ctx)
        self._strategy.on_init()

        feed = ctx._feed  # HistoricalFeed，覆盖 history_start → end_date
        for trade_date, bars in feed.iter_by_date():
            if trade_date < start_date:
                continue

            # 步骤 1：更新撮合引擎的当日 Bar 缓存
            for bar in bars:
                matching.on_bar(bar)

            # 步骤 2：撮合前日订单（D+1 开盘成交）
            matching.process_pending_orders(trade_date)
            bus.dispatch_all()

            # 步骤 3：更新持仓市值
            portfolio.update_prices(bars)

            # 步骤 4：日初回调 + 推送 BarEvent
            executor.set_current_date(trade_date)
            ctx._set_date(trade_date)
            self._strategy.before_trading(trade_date)

            for bar in bars:
                bus.put(BarEvent(bar=bar))
            bus.dispatch_all()

            # 步骤 5：EOD 结算（T+1 解锁）
            bus.put(EndOfDayEvent(trade_date=trade_date))
            bus.dispatch_all()

            # 步骤 6：日末回调
            self._strategy.after_trading(trade_date)

            logger.debug(
                f"[{trade_date}] 纸上交易日结束  总资产 {portfolio.get_total_assets():,.0f}"
            )

        logger.info(
            f"纸上交易完成  最终资产 {portfolio.get_total_assets():,.0f} 元  "
            f"持仓 {len(portfolio.get_all_positions())} 只"
        )

    @staticmethod
    def _load_calendar(store: ParquetStore) -> TradingCalendar:
        trading_days = store.read_calendar("SSE")
        if not trading_days:
            trading_days = store.read_calendar("SZSE")
        if not trading_days:
            raise RuntimeError("本地无交易日历数据，请先运行: python scripts/sync_calendar.py")
        return TradingCalendar(trading_days)

    # ── 组件工厂 ──────────────────────────────────────────────────────────────────

    def _build_components(
        self,
        store: ParquetStore,
        history_start: date,
        history_end: date,
    ) -> tuple[EventBus, PortfolioManager, PreTradeRisk, StrategyContext]:
        """构建引擎所需组件（实盘/纸上交易共用）。"""
        if self._strategy is None:
            raise RuntimeError("请先调用 add_strategy()")

        hist_feed = HistoricalFeed(store, self._symbols, history_start, history_end)
        bus = EventBus()
        portfolio = PortfolioManager(self._config.engine)
        risk = PreTradeRisk(portfolio, self._config.risk)
        ctx = StrategyContext(portfolio, hist_feed)
        return bus, portfolio, risk, ctx

    # ── 静态工具 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _register_handlers(
        bus: EventBus,
        portfolio: PortfolioManager,
        executor: _Executor,
        strategy: Strategy,
    ) -> None:
        bus.subscribe(BarEvent, lambda e: strategy.on_bar(e.bar))
        bus.subscribe(SignalEvent, executor.on_signal)
        bus.subscribe(FillEvent, portfolio.on_fill)
        bus.subscribe(FillEvent, strategy.on_order_update)
        bus.subscribe(RejectEvent, strategy.on_order_update)
        bus.subscribe(EndOfDayEvent, portfolio.settle_eod)

    @staticmethod
    def _drain_executor_events(eq: queue.Queue, bus: EventBus) -> None:
        try:
            while True:
                bus.put(eq.get_nowait())
        except queue.Empty:
            pass

    @staticmethod
    def _drain_bar_queue(
        bar_queue: queue.Queue,
        bus: EventBus,
        portfolio: PortfolioManager,
    ) -> None:
        try:
            while True:
                bar = bar_queue.get_nowait()
                portfolio.update_prices([bar])
                bus.put(BarEvent(bar=bar))
        except queue.Empty:
            pass

