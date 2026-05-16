"""
BacktestEngine：回测主循环。

事件流（每个交易日）：
  1. BarMatchingEngine.process_pending_orders(today)   → 撮合前日订单
  2. strategy.before_trading(today)
  3. 推送当日所有 BarEvent → strategy.on_bar() → SignalEvent
  4. EventBus.dispatch_all()  按优先级处理所有事件
  5. 推送 EndOfDayEvent → portfolio.settle_eod()（T+1 解锁）
  6. strategy.after_trading(today)
  7. 记录绩效快照
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import pandas as pd
from loguru import logger

from cq.core.event_bus import EventBus
from cq.core.events import (
    BarEvent,
    EndOfDayEvent,
    FillEvent,
    OrderEvent,
    RejectEvent,
    SignalEvent,
)
from cq.core.models import Trade
from cq.data.calendar import TradingCalendar
from cq.data.feed.historical import HistoricalFeed
from cq.data.store.parquet_store import ParquetStore
from cq.engine.matching.bar_matching import BarMatchingEngine
from cq.engine.portfolio import PortfolioManager
from cq.execution.simulated import SimulatedExecutor
from cq.performance.metrics import MetricsResult, PerformanceMetrics
from cq.risk.pre_trade import PreTradeRisk
from cq.strategy.base import Strategy, StrategyContext
from cq.utils.config import Config


@dataclass
class BacktestResult:
    """回测结果。"""
    strategy_name: str
    symbols: list[str]
    start_date: date
    end_date: date
    initial_capital: float

    # 绩效指标
    metrics: MetricsResult

    # 详细数据
    equity_curve: pd.Series          # index=date, values=净资产
    trades: list[Trade]              # 完整成交记录
    rejected_orders: list[tuple]     # [(order_id, reason), ...]

    def summary(self) -> str:
        header = (
            f"\n策略：{self.strategy_name}\n"
            f"标的：{', '.join(self.symbols)}\n"
            f"区间：{self.start_date} → {self.end_date}"
            f"（共 {self.metrics.total_trades} 笔交易）\n"
            f"初始资金：{self.initial_capital:,.0f} 元"
        )
        return header + self.metrics.summary()

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "symbols": self.symbols,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "initial_capital": self.initial_capital,
            **self.metrics.to_dict(),
            "rejected_count": len(self.rejected_orders),
        }


class BacktestEngine:
    """
    回测引擎。

    用法：
        engine = BacktestEngine(config)
        engine.add_strategy(MyStrategy(), symbols=["600519.SH"])
        result = engine.run("2022-01-01", "2024-12-31")
        print(result.summary())
    """

    # 进度回调类型：(current_idx, total, trade_date, total_assets) -> None
    ProgressCallback = Callable[[int, int, date, float], None]

    def __init__(
        self,
        config: Config,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        self._config = config
        self._strategy: Optional[Strategy] = None
        self._symbols: list[str] = []
        self._progress_callback = progress_callback

    def add_strategy(self, strategy: Strategy, symbols: list[str]) -> None:
        if not strategy.strategy_id:
            raise ValueError("strategy.strategy_id 不能为空")
        self._strategy = strategy
        self._symbols = list(symbols)

    def run(
        self,
        start_date: str | date,
        end_date: str | date,
    ) -> BacktestResult:
        """运行回测，返回结果。"""
        if self._strategy is None:
            raise RuntimeError("请先调用 add_strategy()")

        start = self._parse_date(start_date)
        end = self._parse_date(end_date)

        logger.info(
            f"开始回测 [{self._strategy.strategy_id}] "
            f"{self._symbols} {start} → {end}"
        )

        # 构建组件
        store = ParquetStore(self._config.data.root_path)
        calendar = self._load_calendar(store)
        feed = HistoricalFeed(
            store, self._symbols, start, end,
            calendar=calendar,
            adjust=self._config.engine.adjust,
        )

        bus = EventBus()
        portfolio = PortfolioManager(self._config.engine)
        risk = PreTradeRisk(portfolio, self._config.risk)
        executor = SimulatedExecutor(bus, portfolio, risk)
        matching = BarMatchingEngine(bus, portfolio, calendar, self._config.engine)
        ctx = StrategyContext(portfolio, feed)

        # 订阅事件
        self._register_handlers(bus, matching, portfolio, executor, self._strategy)

        # 初始化策略
        self._strategy._setup(bus, ctx)
        self._strategy.on_init()

        # 主循环
        equity_curve: dict[date, float] = {}
        all_trades: list[Trade] = []
        rejected: list[tuple] = []

        def on_fill(event: FillEvent) -> None:
            all_trades.append(event.trade)

        def on_reject(event: RejectEvent) -> None:
            rejected.append((event.order_id, event.reason))

        bus.subscribe(FillEvent, on_fill)
        bus.subscribe(RejectEvent, on_reject)

        trade_dates = feed.trade_dates

        for i, (trade_date, bars) in enumerate(feed.iter_by_date()):
            # 步骤 1：更新当日 bar 缓存（撮合引擎需要知道今日价格）
            for bar in bars:
                matching.on_bar(bar)

            # 步骤 2：撮合前日订单（D+1 成交）
            matching.process_pending_orders(trade_date)
            bus.dispatch_all()  # 处理 FillEvent / RejectEvent

            # 更新持仓市值（用于权益计算）
            portfolio.update_prices(bars)

            # 步骤 3：日初回调
            executor.set_current_date(trade_date)
            ctx._set_date(trade_date)
            self._strategy.before_trading(trade_date)

            # 步骤 4：推送 BarEvent，策略产生信号
            for bar in bars:
                bus.put(BarEvent(bar=bar))
            bus.dispatch_all()  # 处理 BarEvent → SignalEvent → OrderEvent

            # 步骤 5：EOD 结算（T+1 解锁）
            eod = EndOfDayEvent(trade_date=trade_date)
            bus.put(eod)
            bus.dispatch_all()

            # 步骤 6：日末回调
            self._strategy.after_trading(trade_date)

            # 步骤 7：记录权益快照
            equity_curve[trade_date] = portfolio.get_total_assets()

            if (i + 1) % 20 == 0 or (i + 1) == len(trade_dates):
                logger.debug(
                    f"进度 {i+1}/{len(trade_dates)} {trade_date} "
                    f"总资产 {portfolio.get_total_assets():,.0f}"
                )
                if self._progress_callback:
                    self._progress_callback(
                        i + 1,
                        len(trade_dates),
                        trade_date,
                        portfolio.get_total_assets(),
                    )

        # 计算指标
        equity_series = pd.Series(equity_curve)
        metrics = PerformanceMetrics().compute(equity_series, all_trades)

        logger.info(
            f"回测完成 | 总收益 {metrics.total_return:+.2%} | "
            f"最大回撤 {metrics.max_drawdown:.2%} | "
            f"夏普 {metrics.sharpe_ratio:.3f}"
        )

        return BacktestResult(
            strategy_name=self._strategy.strategy_id,
            symbols=self._symbols,
            start_date=start,
            end_date=end,
            initial_capital=self._config.engine.initial_capital,
            metrics=metrics,
            equity_curve=equity_series,
            trades=all_trades,
            rejected_orders=rejected,
        )

    # ── 私有方法 ───────────────────────────────────────────────────────────────

    @staticmethod
    def _register_handlers(
        bus: EventBus,
        matching: BarMatchingEngine,
        portfolio: PortfolioManager,
        executor: SimulatedExecutor,
        strategy: Strategy,
    ) -> None:
        """注册事件处理器。"""
        bus.subscribe(BarEvent, lambda e: strategy.on_bar(e.bar))
        bus.subscribe(SignalEvent, executor.on_signal)
        bus.subscribe(OrderEvent, matching.on_order)
        bus.subscribe(FillEvent, portfolio.on_fill)
        bus.subscribe(FillEvent, strategy.on_order_update)
        bus.subscribe(RejectEvent, strategy.on_order_update)
        bus.subscribe(EndOfDayEvent, portfolio.settle_eod)

    def _load_calendar(self, store: ParquetStore) -> TradingCalendar:
        """加载交易日历（上交所）。"""
        trading_days = store.read_calendar("SSE")
        if not trading_days:
            # 尝试从 SZSE 加载
            trading_days = store.read_calendar("SZSE")
        if not trading_days:
            raise RuntimeError(
                "本地无交易日历数据，请先运行: python scripts/sync_calendar.py"
            )
        return TradingCalendar(trading_days)

    @staticmethod
    def _parse_date(d: str | date) -> date:
        if isinstance(d, date):
            return d
        return date.fromisoformat(d)
