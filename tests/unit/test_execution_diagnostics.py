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
            ("O4b", "持仓不足: 请求卖出 200 股，总持仓 100 股"),
            ("O5", "涨停开盘(11.00)，无法买入"),
            ("O5b", "跌停封板(9.00)，无法卖出"),
            ("O6", "限价10.00 < 开盘10.50"),
            ("O7", "停牌: 600519.SH"),
        ],
    )

    assert diagnostics["order_count"] == 9
    assert diagnostics["filled_order_rate"] == 0.111111
    assert diagnostics["capacity_limited_count"] == 1
    assert diagnostics["capacity_limited_fills"] == 1
    assert diagnostics["capacity_rejected_count"] == 1
    assert diagnostics["avg_fill_ratio"] == 0.5
    assert diagnostics["partial_fill_count"] == 1
    assert diagnostics["partial_fill_ratio"] == 0.5
    assert diagnostics["filled_count"] == 1
    assert diagnostics["rejected_count"] == 8
    assert diagnostics["reject_categories"] == {
        "capacity": 1,
        "cash": 1,
        "t1": 1,
        "position": 1,
        "limit_up": 1,
        "limit_down": 1,
        "limit_order": 1,
        "suspended": 1,
    }
    assert diagnostics["rejected_by_limit_up"] == 1
    assert diagnostics["rejected_by_limit_down"] == 1
    assert diagnostics["rejected_by_suspended"] == 1
    assert diagnostics["rejected_by_cash"] == 1
    assert diagnostics["rejected_by_t1"] == 1
    assert diagnostics["rejected_by_position"] == 1
    assert diagnostics["rejected_by_limit_order"] == 1
    assert diagnostics["rejected_by_missing_bar"] == 0
    assert diagnostics["top_reject_reasons"][0]["count"] == 1
