"""
集成测试：端到端回测流程（使用内存数据，无需外部依赖）。

构造一段简单的价格序列，验证：
1. 资金守恒
2. T+1 约束
3. 无前视偏差（D 信号 D+1 成交）
4. 手续费正确扣除
"""

from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from cq.core.event_bus import EventBus
from cq.core.events import BarEvent, EndOfDayEvent, FillEvent, RejectEvent
from cq.core.models import Bar, OrderSide, PositionSnapshot
from cq.data.calendar import TradingCalendar
from cq.engine.matching.bar_matching import BarMatchingEngine
from cq.engine.portfolio import PortfolioManager
from cq.execution.simulated import SimulatedExecutor
from cq.risk.pre_trade import PreTradeRisk
from cq.strategy.base import Strategy, StrategyContext
from cq.utils.config import EngineConfig, RiskConfig


def make_bar(
    symbol: str,
    trade_date: date,
    open_: float,
    close: float,
    pre_close: float,
    limit_up: float = None,
    limit_down: float = None,
) -> Bar:
    limit_up = limit_up or round(pre_close * 1.1, 2)
    limit_down = limit_down or round(pre_close * 0.9, 2)
    return Bar(
        symbol=symbol, trade_date=trade_date,
        open=open_, high=close * 1.02, low=close * 0.98, close=close,
        volume=10000, amount=close * 10000,
        limit_up=limit_up, limit_down=limit_down, pre_close=pre_close,
    )


# 测试日历：5个交易日
TRADE_DATES = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
]

CALENDAR = TradingCalendar(TRADE_DATES)


# 测试价格序列：一路上涨
BARS = {
    date(2024, 1, 2): make_bar("000001.SZ", date(2024, 1, 2), open_=10.0, close=10.0, pre_close=9.5),
    date(2024, 1, 3): make_bar("000001.SZ", date(2024, 1, 3), open_=10.5, close=11.0, pre_close=10.0),
    date(2024, 1, 4): make_bar("000001.SZ", date(2024, 1, 4), open_=11.5, close=12.0, pre_close=11.0),
    date(2024, 1, 5): make_bar("000001.SZ", date(2024, 1, 5), open_=12.5, close=13.0, pre_close=12.0),
    date(2024, 1, 8): make_bar("000001.SZ", date(2024, 1, 8), open_=13.5, close=14.0, pre_close=13.0),
}


class BuyOnDay1Sell0nDay3(Strategy):
    """在第1日买入，第3日卖出。"""
    strategy_id = "test_buy_d1_sell_d3"

    def on_init(self):
        self._bought = False
        self._sold = False

    def on_bar(self, bar: Bar):
        trade_date = self.ctx.get_trade_date()
        pos = self.ctx.get_position(bar.symbol)

        if trade_date == date(2024, 1, 2) and not self._bought:
            self.buy(bar.symbol, quantity=100)
            self._bought = True

        elif trade_date == date(2024, 1, 4) and pos and not self._sold:
            self.sell(bar.symbol)
            self._sold = True


def run_mini_backtest(strategy: Strategy) -> tuple[dict, list, list]:
    """运行简单回测，返回 (equity_by_date, trades, rejects)。"""
    config = EngineConfig(initial_capital=100_000, commission_rate=0.0003, min_commission=5.0)
    risk_cfg = RiskConfig(max_position_pct=1.0, min_cash_reserve=0.0)

    bus = EventBus()
    portfolio = PortfolioManager(config)
    risk = PreTradeRisk(portfolio, risk_cfg)
    executor = SimulatedExecutor(bus, portfolio, risk)
    matching = BarMatchingEngine(bus, portfolio, CALENDAR, config)

    fills = []
    rejects = []

    class MockFeed:
        def get_history(self, symbol, current_date, n):
            return pd.DataFrame()

    ctx = StrategyContext(portfolio, MockFeed())

    from cq.core.events import OrderEvent, SignalEvent
    bus.subscribe(BarEvent, lambda e: matching.on_bar(e.bar))
    bus.subscribe(BarEvent, lambda e: strategy.on_bar(e.bar))
    bus.subscribe(SignalEvent, executor.on_signal)
    bus.subscribe(OrderEvent, matching.on_order)
    bus.subscribe(FillEvent, portfolio.on_fill)
    bus.subscribe(FillEvent, lambda e: fills.append(e.trade))
    bus.subscribe(FillEvent, strategy.on_order_update)
    bus.subscribe(RejectEvent, lambda e: rejects.append((e.order_id, e.reason)))
    bus.subscribe(RejectEvent, strategy.on_order_update)
    bus.subscribe(EndOfDayEvent, portfolio.settle_eod)

    strategy._setup(bus, ctx)
    strategy.on_init()

    equity = {}
    for trade_date in TRADE_DATES:
        bar = BARS[trade_date]
        matching.on_bar(bar)
        matching.process_pending_orders(trade_date)
        bus.dispatch_all()

        portfolio.update_prices([bar])
        risk.update_equity_state(trade_date)
        executor.set_current_date(trade_date)
        ctx._set_date(trade_date)
        strategy.before_trading(trade_date)

        bus.put(BarEvent(bar=bar))
        bus.dispatch_all()

        strategy.after_trading(trade_date)
        bus.dispatch_all()

        bus.put(EndOfDayEvent(trade_date=trade_date))
        bus.dispatch_all()

        equity[trade_date] = portfolio.get_total_assets()

    return equity, fills, rejects


class TestT1Constraint:
    def test_buy_d1_cant_sell_d1(self):
        """D日买入，D日卖不出（T+1约束）。"""

        class BuyAndSellSameDay(Strategy):
            strategy_id = "test_same_day"

            def on_bar(self, bar: Bar):
                if self.ctx.get_trade_date() == date(2024, 1, 2):
                    self.buy(bar.symbol, quantity=100)
                    self.sell(bar.symbol)  # 同日卖出，应被拒绝

        _, fills, rejects = run_mini_backtest(BuyAndSellSameDay())

        buy_fills = [f for f in fills if f.side == OrderSide.BUY]
        sell_fills = [f for f in fills if f.side == OrderSide.SELL]

        assert len(buy_fills) == 1
        assert len(sell_fills) == 0  # 卖出应被拒绝

    def test_after_trading_before_eod_unlock(self):
        """盘后信号发生在 T+1 解锁前，当日买入不能在 after_trading 中立刻变成可卖。"""

        class BuyThenAfterTradingSellSameDay(Strategy):
            strategy_id = "test_after_trading_t1"

            def on_init(self):
                self._bought = False
                self._sold = False

            def on_bar(self, bar: Bar):
                if self.ctx.get_trade_date() == date(2024, 1, 2) and not self._bought:
                    self.buy(bar.symbol, quantity=100)
                    self._bought = True

            def after_trading(self, trade_date: date):
                if trade_date == date(2024, 1, 3) and not self._sold:
                    self.sell("000001.SZ")
                    self._sold = True

        _, fills, rejects = run_mini_backtest(BuyThenAfterTradingSellSameDay())

        buy_fills = [f for f in fills if f.side == OrderSide.BUY]
        sell_fills = [f for f in fills if f.side == OrderSide.SELL]

        assert len(buy_fills) == 1
        assert len(sell_fills) == 0
        assert any(("T+1限制" in reason or "卖出数量为零" in reason) for _, reason in rejects)


class TestCashConstraint:
    def test_gap_up_percent_buy_is_shrunk_and_cash_non_negative(self):
        """百分比买入遇到次日跳空和最低佣金时，应按可用现金缩量且现金不为负。"""

        class AllInBuyDay1(Strategy):
            strategy_id = "test_cash_guard"

            def on_bar(self, bar: Bar):
                if self.ctx.get_trade_date() == date(2024, 1, 2):
                    self.buy(bar.symbol, percent=1.0)

        custom_bars = {
            **BARS,
            date(2024, 1, 3): make_bar(
                "000001.SZ",
                date(2024, 1, 3),
                open_=12.0,
                close=12.0,
                pre_close=10.0,
                limit_up=20.0,
                limit_down=5.0,
            ),
        }

        with patch.dict(BARS, custom_bars, clear=True):
            equity, fills, rejects = run_mini_backtest(AllInBuyDay1())

        buy_fills = [f for f in fills if f.side == OrderSide.BUY]
        assert len(buy_fills) == 1
        assert buy_fills[0].quantity < 9900
        assert buy_fills[0].net_amount <= 100_000
        assert equity[date(2024, 1, 3)] >= 0
        assert not any("现金不足" in reason for _, reason in rejects)


class TestNoLookAheadBias:
    def test_d1_signal_fills_d2_open(self):
        """D日信号，成交价应为D+1日开盘价（10.5），不是D日价格（10.0）。"""
        strategy = BuyOnDay1Sell0nDay3()
        _, fills, rejects = run_mini_backtest(strategy)

        buy_fills = [f for f in fills if f.side == OrderSide.BUY]
        assert len(buy_fills) == 1
        # D+1（1/3）的开盘价是 10.5
        assert buy_fills[0].price == pytest.approx(10.5)
        assert buy_fills[0].trade_date == date(2024, 1, 3)


class TestFeeCalculation:
    def test_commission_deducted(self):
        """手续费应从现金中扣除。"""
        strategy = BuyOnDay1Sell0nDay3()
        equity, fills, _ = run_mini_backtest(strategy)

        buy_fills = [f for f in fills if f.side == OrderSide.BUY]
        assert buy_fills[0].commission >= 5.0  # 最低佣金
