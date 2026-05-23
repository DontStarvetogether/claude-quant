"""单元测试：撮合执行诊断。"""

from datetime import date, datetime

from cq.core.models import OrderSide, Trade
from cq.engine.backtest_engine import BacktestEngine


def make_trade(
    quantity: int = 500,
    requested_quantity: int = 1000,
    capacity_limited: bool = True,
) -> Trade:
    return Trade(
        trade_id="T1",
        order_id="O1",
        symbol="600519.SH",
        side=OrderSide.BUY,
        quantity=quantity,
        price=10.0,
        amount=quantity * 10.0,
        commission=5.0,
        stamp_tax=0.0,
        trade_time=datetime(2024, 1, 3, 9, 30),
        trade_date=date(2024, 1, 3),
        requested_quantity=requested_quantity,
        capacity_limited=capacity_limited,
        capacity_limit_qty=quantity,
    )


def test_execution_diagnostics_classifies_rejections():
    diagnostics = BacktestEngine._execution_diagnostics(
        trades=[make_trade()],
        rejected=[
            ("O2", "成交容量不足: 请求1000股，容量上限0股"),
            ("O3", "可用现金不足（现金 100 - 已预留 0 = 100，含费用需要 1000）"),
            ("O4", "T+1限制: 可卖0股，请求卖100股"),
            ("O5", "涨停开盘(11.00)，无法买入"),
            ("O6", "限价10.00 < 开盘10.50"),
        ],
    )

    assert diagnostics["capacity_limited_count"] == 1
    assert diagnostics["capacity_rejected_count"] == 1
    assert diagnostics["avg_fill_ratio"] == 0.5
    assert diagnostics["partial_fill_count"] == 1
    assert diagnostics["filled_count"] == 1
    assert diagnostics["rejected_count"] == 5
    assert diagnostics["reject_categories"] == {
        "capacity": 1,
        "cash": 1,
        "position": 1,
        "limit_price": 1,
        "limit_order": 1,
    }
    assert diagnostics["top_reject_reasons"][0]["count"] == 1
