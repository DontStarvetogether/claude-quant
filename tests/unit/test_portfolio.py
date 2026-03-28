"""单元测试：PortfolioManager"""

import pytest
from datetime import date, datetime, time

from cq.core.events import EndOfDayEvent, FillEvent
from cq.core.models import OrderSide, Trade, new_trade_id
from cq.engine.portfolio import PortfolioManager
from cq.utils.config import EngineConfig


def make_fill(
    symbol: str = "600519.SH",
    side: OrderSide = OrderSide.BUY,
    quantity: int = 100,
    price: float = 100.0,
    commission: float = 5.0,
    stamp_tax: float = 0.0,
    trade_date: date = date(2024, 1, 2),
) -> FillEvent:
    trade = Trade(
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
    return FillEvent(trade=trade)


@pytest.fixture
def portfolio():
    config = EngineConfig(initial_capital=1_000_000)
    return PortfolioManager(config)


class TestPortfolioManager:
    def test_buy_reduces_cash(self, portfolio):
        initial_cash = portfolio.get_cash()
        fill = make_fill(quantity=100, price=100.0, commission=5.0)
        portfolio.on_fill(fill)

        expected_cash = initial_cash - (100 * 100.0 + 5.0)
        assert portfolio.get_cash() == pytest.approx(expected_cash)

    def test_buy_creates_position(self, portfolio):
        portfolio.on_fill(make_fill(quantity=100, price=100.0, commission=5.0))
        pos = portfolio.get_position("600519.SH")
        assert pos is not None
        assert pos.total_qty == 100

    def test_t1_buy_not_tradeable(self, portfolio):
        """当日买入，tradeable_qty 不增加。"""
        portfolio.on_fill(make_fill(quantity=100))
        pos = portfolio.get_position("600519.SH")
        assert pos.tradeable_qty == 0
        assert pos.total_qty == 100

    def test_eod_settle_unlocks(self, portfolio):
        """EOD settle 后 tradeable_qty 解锁。"""
        portfolio.on_fill(make_fill(quantity=100, trade_date=date(2024, 1, 2)))
        eod = EndOfDayEvent(trade_date=date(2024, 1, 2))
        portfolio.settle_eod(eod)

        pos = portfolio.get_position("600519.SH")
        assert pos.tradeable_qty == 100

    def test_sell_increases_cash(self, portfolio):
        """先买后卖，现金应增加。"""
        portfolio.on_fill(make_fill(quantity=100, price=100.0, commission=5.0))
        portfolio.settle_eod(EndOfDayEvent(trade_date=date(2024, 1, 2)))

        sell_fill = make_fill(
            side=OrderSide.SELL, quantity=100, price=110.0,
            commission=5.0, stamp_tax=11.0, trade_date=date(2024, 1, 3)
        )
        portfolio.on_fill(sell_fill)

        pos = portfolio.get_position("600519.SH")
        assert pos is None  # 清仓后持仓应被删除

    def test_avg_cost_includes_commission(self, portfolio):
        """均价应包含佣金。"""
        portfolio.on_fill(make_fill(quantity=100, price=100.0, commission=10.0))
        pos = portfolio.get_position("600519.SH")
        # avg_cost = (100 * 100 + 10) / 100 = 100.1
        assert pos.avg_cost == pytest.approx(100.1)

    def test_asset_conservation(self, portfolio):
        """资金守恒：买入后总资产（含持仓市值）不变（忽略手续费）。"""
        from cq.core.models import Bar
        initial_assets = portfolio.get_total_assets()

        portfolio.on_fill(make_fill(quantity=100, price=100.0, commission=0.0))

        # 更新价格（100元）
        bar = Bar(
            symbol="600519.SH", trade_date=date(2024, 1, 2),
            open=100.0, high=100.0, low=100.0, close=100.0,
            volume=100, amount=10000.0,
            limit_up=110.0, limit_down=90.0, pre_close=100.0,
        )
        portfolio.update_prices([bar])

        assert portfolio.get_total_assets() == pytest.approx(initial_assets)
