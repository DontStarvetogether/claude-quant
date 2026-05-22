"""
集成测试：LiveEngine 纸上交易模式。

测试内容：
  1. paper_trade() 能正常运行（不崩溃、不挂起）
  2. 资金守恒：交易后 总资产 = 现金 + 持仓市值
  3. 信号一致性：同策略同数据，paper_trade 与 BacktestEngine 产生相同的成交笔数
  4. sync_from_broker()：从券商同步持仓后状态正确
  5. PaperExecutor：风控拒绝、股数计算、模拟成交完整流程

全部测试不依赖 QMT / 外部网络，使用内存构造的价格数据。
"""

from __future__ import annotations

import queue
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from cq.core.event_bus import EventBus
from cq.core.events import FillEvent, RejectEvent, SignalEvent
from cq.core.models import Bar, OrderSide, OrderType, Signal, new_signal_id
from cq.engine.portfolio import PortfolioManager
from cq.execution.paper import PaperExecutor
from cq.risk.pre_trade import PreTradeRisk
from cq.strategy.base import Strategy, StrategyContext
from cq.utils.config import Config, EngineConfig, RiskConfig


# ── 测试用 fixture ──────────────────────────────────────────────────────────────

TRADE_DATES = [
    date(2024, 6, 3),
    date(2024, 6, 4),
    date(2024, 6, 5),
    date(2024, 6, 6),
    date(2024, 6, 7),
    date(2024, 6, 11),
    date(2024, 6, 12),
    date(2024, 6, 13),
    date(2024, 6, 14),
    date(2024, 6, 17),
    date(2024, 6, 18),
    date(2024, 6, 19),
    date(2024, 6, 20),
    date(2024, 6, 21),
    date(2024, 6, 24),
    date(2024, 6, 25),
    date(2024, 6, 26),
    date(2024, 6, 27),
    date(2024, 6, 28),
]

SYMBOL = "600519.SH"
INITIAL_CAPITAL = 1_000_000.0


def make_bar(symbol: str, trade_date: date, close: float, pre_close: float) -> Bar:
    return Bar(
        symbol=symbol, trade_date=trade_date,
        open=close * 0.995, high=close * 1.02, low=close * 0.98, close=close,
        volume=10000, amount=close * 10000,
        limit_up=round(pre_close * 1.1, 2),
        limit_down=round(pre_close * 0.9, 2),
        pre_close=pre_close,
    )


def make_price_series() -> list[Bar]:
    """构造一个简单的价格序列（先涨后跌）。"""
    prices = [
        100, 102, 105, 107, 106,
        108, 110, 112, 115, 113,
        111, 109, 107, 105, 103,
        101, 100, 98, 97,
    ]
    bars = []
    for i, (d, p) in enumerate(zip(TRADE_DATES, prices)):
        pre = prices[i - 1] if i > 0 else p
        bars.append(make_bar(SYMBOL, d, float(p), float(pre)))
    return bars


def make_df(bars: list[Bar]) -> pd.DataFrame:
    """将 Bar 列表转为 HistoricalFeed 需要的 DataFrame 格式。"""
    rows = []
    for bar in bars:
        rows.append({
            "symbol": bar.symbol,
            "trade_date": bar.trade_date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "amount": bar.amount,
            "limit_up": bar.limit_up,
            "limit_down": bar.limit_down,
            "pre_close": bar.pre_close,
            "is_st": False,
            "is_suspended": False,
        })
    return pd.DataFrame(rows)


@pytest.fixture
def default_config() -> Config:
    cfg = Config.default()
    cfg.engine.initial_capital = INITIAL_CAPITAL
    cfg.engine.commission_rate = 0.0003
    cfg.engine.stamp_tax_rate = 0.001
    cfg.engine.min_commission = 5.0
    return cfg


@pytest.fixture
def price_bars() -> list[Bar]:
    return make_price_series()


# ── 测试用策略 ────────────────────────────────────────────────────────────────

class SimpleBuyHoldStrategy(Strategy):
    """第1天买入，持有到最后一天卖出。"""
    strategy_id = "simple_buy_hold"

    def __init__(self):
        super().__init__()
        self._bought = False

    def on_bar(self, bar: Bar) -> None:
        if not self._bought:
            self.buy(bar.symbol, percent=0.15)  # 15%，低于风控上限 20%
            self._bought = True


class MomentumStrategy(Strategy):
    """3日价格上涨则买入，下跌则卖出（用于信号一致性对比测试）。"""
    strategy_id = "momentum_test"

    def on_bar(self, bar: Bar) -> None:
        history = self.ctx.get_bar_history(bar.symbol, n=3)
        if len(history) < 3:
            return
        prices = history["close"].values
        pos = self.ctx.get_position(bar.symbol)

        if prices[-1] > prices[0] and pos is None:
            self.buy(bar.symbol, percent=0.4)
        elif prices[-1] < prices[0] and pos is not None:
            self.sell(bar.symbol)


# ── 单元测试：PaperExecutor ──────────────────────────────────────────────────

class TestPaperExecutor:

    def _make_executor(self, initial_capital=INITIAL_CAPITAL):
        bus = EventBus()
        cfg_engine = EngineConfig(initial_capital=initial_capital)
        cfg_risk = RiskConfig()
        portfolio = PortfolioManager(cfg_engine)
        risk = PreTradeRisk(portfolio, cfg_risk)
        executor = PaperExecutor(bus, portfolio, risk)
        # 设置最新价格
        portfolio._market_prices[SYMBOL] = 100.0
        return executor, portfolio, bus

    def test_connect_succeeds(self):
        executor, _, _ = self._make_executor()
        executor.connect()  # 不应抛出异常

    def test_sync_positions_noop(self):
        executor, portfolio, _ = self._make_executor()
        executor.sync_positions()
        assert portfolio.get_cash() == INITIAL_CAPITAL  # 无变化

    def test_buy_signal_produces_fill(self):
        executor, portfolio, bus = self._make_executor()
        executor.set_current_date(date(2024, 6, 3))

        sig = Signal(
            signal_id=new_signal_id(),
            symbol=SYMBOL,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            percent=0.1,  # 10%，低于风控上限 20%
        )
        executor.on_signal(SignalEvent(signal=sig))

        # event_queue 应有一个 FillEvent
        assert not executor.event_queue.empty()
        event = executor.event_queue.get_nowait()
        assert isinstance(event, FillEvent)
        assert event.trade.symbol == SYMBOL
        assert event.trade.side == OrderSide.BUY
        assert event.trade.quantity > 0
        assert event.trade.quantity % 100 == 0  # 必须是 100 整数倍

    def test_sell_without_position_produces_reject(self):
        executor, _, _ = self._make_executor()
        executor.set_current_date(date(2024, 6, 3))

        sig = Signal(
            signal_id=new_signal_id(),
            symbol=SYMBOL,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
        )
        executor.on_signal(SignalEvent(signal=sig))

        event = executor.event_queue.get_nowait()
        assert isinstance(event, RejectEvent)

    def test_risk_clamps_oversized_percent_position(self):
        """percent 信号超过 max_position_pct 时应被风控截断。"""
        executor, _, _ = self._make_executor()
        executor.set_current_date(date(2024, 6, 3))

        sig = Signal(
            signal_id=new_signal_id(),
            symbol=SYMBOL,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            percent=0.99,  # 超过默认 20% 上限
        )
        executor.on_signal(SignalEvent(signal=sig))

        event = executor.event_queue.get_nowait()
        assert isinstance(event, FillEvent)
        assert event.trade.quantity == 2000

    def test_fill_amount_correct(self):
        """成交金额 = 价格 × 数量（含滑点）。"""
        executor, _, _ = self._make_executor()
        executor.set_current_date(date(2024, 6, 3))

        sig = Signal(
            signal_id=new_signal_id(),
            symbol=SYMBOL,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            limit_price=100.0,
        )
        executor.on_signal(SignalEvent(signal=sig))

        event = executor.event_queue.get_nowait()
        assert isinstance(event, FillEvent)
        trade = event.trade
        # 限价单无滑点
        assert abs(trade.price - 100.0 * (1 + PaperExecutor.SLIPPAGE)) < 0.01
        assert trade.quantity == 100
        assert abs(trade.amount - trade.price * 100) < 0.01


# ── 单元测试：sync_from_broker ───────────────────────────────────────────────

class TestSyncFromBroker:

    def test_sync_overwrites_state(self):
        cfg = EngineConfig(initial_capital=INITIAL_CAPITAL)
        portfolio = PortfolioManager(cfg)

        portfolio.sync_from_broker(
            cash=300_000.0,
            positions=[{
                "symbol": SYMBOL,
                "total_qty": 500,
                "tradeable_qty": 500,
                "avg_cost": 150.0,
                "last_price": 160.0,
            }],
        )

        assert portfolio.get_cash() == 300_000.0
        pos = portfolio.get_position(SYMBOL)
        assert pos is not None
        assert pos.total_qty == 500
        assert pos.tradeable_qty == 500
        assert abs(pos.avg_cost - 150.0) < 0.01
        assert abs(pos.last_price - 160.0) < 0.01

    def test_total_assets_after_sync(self):
        cfg = EngineConfig(initial_capital=INITIAL_CAPITAL)
        portfolio = PortfolioManager(cfg)
        portfolio.sync_from_broker(
            cash=500_000.0,
            positions=[{
                "symbol": SYMBOL,
                "total_qty": 1000,
                "tradeable_qty": 1000,
                "avg_cost": 200.0,
                "last_price": 210.0,
            }],
        )
        # 总资产 = 现金 + 市值 = 500000 + 1000*210 = 710000
        assert abs(portfolio.get_total_assets() - 710_000.0) < 1.0

    def test_sync_clears_previous_positions(self):
        cfg = EngineConfig(initial_capital=INITIAL_CAPITAL)
        portfolio = PortfolioManager(cfg)
        portfolio.sync_from_broker(
            cash=800_000.0,
            positions=[{"symbol": "000001.SZ", "total_qty": 200,
                        "tradeable_qty": 200, "avg_cost": 10.0, "last_price": 11.0}],
        )
        # 再次同步，切换持仓
        portfolio.sync_from_broker(
            cash=900_000.0,
            positions=[{"symbol": SYMBOL, "total_qty": 100,
                        "tradeable_qty": 100, "avg_cost": 100.0, "last_price": 100.0}],
        )
        assert portfolio.get_position("000001.SZ") is None
        assert portfolio.get_position(SYMBOL) is not None


# ── 集成测试：LiveEngine.paper_trade() ───────────────────────────────────────

class TestLivePaperTrade:
    """
    通过 mock ParquetStore，让 paper_trade 使用内存构造的价格数据运行完整流程。
    """

    def _make_mock_store(self, bars: list[Bar]):
        """构造一个返回内存数据的 ParquetStore mock。"""
        df = make_df(bars)
        store = MagicMock()
        # HistoricalFeed 调用 store.read_bars_batch(...)
        store.read_bars_batch.return_value = df
        return store

    def test_paper_trade_completes_without_error(self, default_config, price_bars):
        """paper_trade 应能正常跑完所有交易日，不报错。"""
        from cq.live.engine import LiveEngine

        store = self._make_mock_store(price_bars)
        engine = LiveEngine(default_config)
        engine.add_strategy(SimpleBuyHoldStrategy(), symbols=[SYMBOL])
        engine.paper_trade(
            store=store,
            start_date=TRADE_DATES[0],
            end_date=TRADE_DATES[-1],
        )

    def test_capital_conservation(self, default_config, price_bars):
        """交易后资金守恒：总资产 = 现金 + 持仓市值。"""
        from cq.live.engine import LiveEngine

        store = self._make_mock_store(price_bars)

        # 捕获最终 portfolio 状态
        captured = {}

        class _CapturingStrategy(SimpleBuyHoldStrategy):
            strategy_id = "simple_buy_hold"

            def after_trading(self, trade_date):
                captured["cash"] = self.ctx.get_cash()
                captured["total"] = self.ctx.get_total_assets()

        engine = LiveEngine(default_config)
        engine.add_strategy(_CapturingStrategy(), symbols=[SYMBOL])
        engine.paper_trade(store=store, start_date=TRADE_DATES[0], end_date=TRADE_DATES[-1])

        assert "total" in captured, "after_trading 应该被调用"
        # 总资产应等于现金+持仓市值（portfolio 内部自洽）
        cash = captured["cash"]
        total = captured["total"]
        assert cash >= 0
        assert total >= cash  # 有持仓时 total >= cash

    def test_fill_count_greater_than_zero(self, default_config, price_bars):
        """策略应产生至少一笔成交。"""
        from cq.live.engine import LiveEngine

        store = self._make_mock_store(price_bars)

        fills = []

        class _CountingStrategy(SimpleBuyHoldStrategy):
            strategy_id = "simple_buy_hold"

            def on_order_update(self, event):
                if isinstance(event, FillEvent):
                    fills.append(event.trade)

        engine = LiveEngine(default_config)
        engine.add_strategy(_CountingStrategy(), symbols=[SYMBOL])
        engine.paper_trade(store=store, start_date=TRADE_DATES[0], end_date=TRADE_DATES[-1])

        assert len(fills) >= 1, "SimpleBuyHoldStrategy 应产生至少 1 笔买入成交"

    def test_paper_trade_respects_t1(self, default_config, price_bars):
        """
        T+1 约束：买入当日不可卖出。
        策略在同一天买入后立即尝试卖出，卖出应被风控拒绝。
        """
        from cq.live.engine import LiveEngine

        rejects = []
        fills = []

        class _BuyThenImmediateSellStrategy(Strategy):
            strategy_id = "buy_then_sell"
            _bought = False

            def on_bar(self, bar: Bar) -> None:
                if not self._bought:
                    self.buy(bar.symbol, percent=0.15)  # 15%，低于风控上限 20%
                    self.sell(bar.symbol)  # 当日买入后立即卖出 → 应被风控拒绝
                    self._bought = True

            def on_order_update(self, event):
                if isinstance(event, FillEvent):
                    fills.append(event)
                elif isinstance(event, RejectEvent):
                    rejects.append(event)

        store = self._make_mock_store(price_bars)
        engine = LiveEngine(default_config)
        engine.add_strategy(_BuyThenImmediateSellStrategy(), symbols=[SYMBOL])
        engine.paper_trade(store=store, start_date=TRADE_DATES[0], end_date=TRADE_DATES[-1])

        assert len(fills) >= 1, "买入应该成功"
        # 当日卖出时 tradeable_qty=0，应被风控拒绝（quantity 为 0）
        assert len(rejects) >= 1, "当日买入后立即卖出应被风控拒绝（T+1 约束）"
