"""单元测试：核心数据模型"""

import pytest
from datetime import date, datetime

from cq.core.models import (
    Bar, Order, Trade, Position, Account,
    OrderSide, OrderType, OrderStatus,
)


class TestBar:
    def test_pct_change(self):
        bar = Bar(
            symbol="600519.SH", trade_date=date(2024, 1, 2),
            open=100.0, high=110.0, low=95.0, close=108.0,
            volume=1000, amount=100000.0,
            limit_up=110.0, limit_down=90.0, pre_close=100.0,
        )
        assert bar.pct_change == pytest.approx(0.08)

    def test_pct_change_zero_pre_close(self):
        bar = Bar(
            symbol="600519.SH", trade_date=date(2024, 1, 2),
            open=100.0, high=110.0, low=95.0, close=108.0,
            volume=0, amount=0.0,
            limit_up=110.0, limit_down=90.0, pre_close=0.0,
        )
        assert bar.pct_change == 0.0


class TestPosition:
    def test_market_value(self):
        pos = Position(symbol="000001.SZ", total_qty=1000, last_price=15.5)
        assert pos.market_value == pytest.approx(15500.0)

    def test_unrealized_pnl(self):
        pos = Position(symbol="000001.SZ", total_qty=1000, avg_cost=10.0, last_price=12.0)
        assert pos.unrealized_pnl == pytest.approx(2000.0)
        assert pos.unrealized_pnl_pct == pytest.approx(0.2)

    def test_snapshot_is_frozen(self):
        pos = Position(symbol="000001.SZ", total_qty=100, tradeable_qty=100)
        snap = pos.snapshot()
        with pytest.raises((AttributeError, TypeError)):
            snap.total_qty = 200  # type: ignore

    def test_t1_initial_state(self):
        """买入后 tradeable_qty 不增加，today_bought_qty 增加。"""
        pos = Position(symbol="600519.SH")
        pos.total_qty += 100
        pos.today_bought_qty += 100
        assert pos.tradeable_qty == 0
        assert pos.today_bought_qty == 100


class TestAccount:
    def test_total_assets(self):
        account = Account(initial_capital=1_000_000, cash=500_000)
        account.positions["600519.SH"] = Position(
            symbol="600519.SH", total_qty=100, last_price=1700.0
        )
        assert account.total_assets == pytest.approx(500_000 + 170_000)

    def test_snapshot_independence(self):
        """快照应与原账户独立（修改原账户不影响快照）。"""
        account = Account(initial_capital=1_000_000, cash=1_000_000)
        snap = account.snapshot()
        account.cash = 500_000
        assert snap.cash == 1_000_000
