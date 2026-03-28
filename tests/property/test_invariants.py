"""
属性测试：验证系统数学不变式。

使用 hypothesis 进行基于属性的测试。
"""

from __future__ import annotations

import pytest
from datetime import date, datetime, time

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cq.core.events import EndOfDayEvent, FillEvent
from cq.core.models import OrderSide, Trade, new_trade_id
from cq.engine.portfolio import PortfolioManager
from cq.utils.config import EngineConfig


def make_trade(
    symbol: str,
    side: OrderSide,
    quantity: int,
    price: float,
    commission: float,
    stamp_tax: float = 0.0,
    trade_date: date = date(2024, 1, 2),
) -> Trade:
    return Trade(
        trade_id=new_trade_id(),
        order_id="O001",
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        amount=price * quantity,
        commission=commission,
        stamp_tax=stamp_tax,
        trade_time=datetime.combine(trade_date, time(9, 30)),
        trade_date=trade_date,
    )


@given(
    initial_capital=st.floats(min_value=100_000, max_value=10_000_000),
    price=st.floats(min_value=1.0, max_value=10_000.0),
    quantity=st.integers(min_value=100, max_value=10_000).map(lambda x: x // 100 * 100),
)
@settings(max_examples=100)
def test_cash_conservation_on_buy(initial_capital, price, quantity):
    """买入后：cash + 持仓市值 = 初始资金（无手续费时）。"""
    assume(price * quantity <= initial_capital * 0.95)

    config = EngineConfig(initial_capital=initial_capital, commission_rate=0.0, min_commission=0.0)
    portfolio = PortfolioManager(config)

    trade = make_trade("000001.SZ", OrderSide.BUY, quantity, price, commission=0.0)
    portfolio.on_fill(FillEvent(trade=trade))

    # 更新价格
    from cq.core.models import Bar
    bar = Bar(
        symbol="000001.SZ", trade_date=date(2024, 1, 2),
        open=price, high=price, low=price, close=price,
        volume=1000, amount=price*1000,
        limit_up=price*1.1, limit_down=price*0.9, pre_close=price,
    )
    portfolio.update_prices([bar])

    assert abs(portfolio.get_total_assets() - initial_capital) < 0.01, (
        f"资金守恒违反：初始 {initial_capital}，买入后总资产 {portfolio.get_total_assets()}"
    )


@given(
    quantity=st.integers(min_value=100, max_value=10_000).map(lambda x: x // 100 * 100),
)
@settings(max_examples=50)
def test_t1_invariant(quantity):
    """任意买入后，当日 tradeable_qty 必须为 0。"""
    config = EngineConfig(initial_capital=10_000_000)
    portfolio = PortfolioManager(config)

    trade = make_trade("000001.SZ", OrderSide.BUY, quantity, price=10.0, commission=5.0)
    portfolio.on_fill(FillEvent(trade=trade))

    pos = portfolio.get_position("000001.SZ")
    assert pos is not None
    assert pos.tradeable_qty == 0, f"T+1违反：买入后 tradeable_qty={pos.tradeable_qty}，应为0"
    assert pos.total_qty == quantity


@given(
    quantity=st.integers(min_value=100, max_value=10_000).map(lambda x: x // 100 * 100),
)
@settings(max_examples=50)
def test_eod_settle_unlocks_all(quantity):
    """EOD settle 后，tradeable_qty 应等于 total_qty（当日只有买入无卖出）。"""
    config = EngineConfig(initial_capital=10_000_000)
    portfolio = PortfolioManager(config)

    trade = make_trade("000001.SZ", OrderSide.BUY, quantity, price=10.0, commission=5.0)
    portfolio.on_fill(FillEvent(trade=trade))

    eod = EndOfDayEvent(trade_date=date(2024, 1, 2))
    portfolio.settle_eod(eod)

    pos = portfolio.get_position("000001.SZ")
    assert pos.tradeable_qty == quantity
    assert pos.total_qty == quantity
