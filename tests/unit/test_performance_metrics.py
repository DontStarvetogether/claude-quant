"""单元测试：绩效指标计算。"""

from datetime import date
from datetime import datetime

import pandas as pd
import pytest

from cq.core.models import AccountSnapshot, OrderSide, PositionSnapshot, Trade
from cq.performance.metrics import MetricsResult, PerformanceMetrics


def make_metrics() -> MetricsResult:
    return MetricsResult(
        total_return=0.0,
        annual_return=0.0,
        max_drawdown=0.0,
        volatility=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        total_trades=0,
        win_rate=0.0,
        avg_profit=0.0,
        avg_loss=0.0,
        profit_factor=0.0,
        avg_hold_days=0.0,
        total_fees=0.0,
        final_value=1_000_000.0,
        initial_value=1_000_000.0,
        max_drawdown_start=None,
        max_drawdown_end=None,
    )


def test_compute_benchmark_treats_benchmark_input_as_returns():
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    strategy_returns = pd.Series([0.01, 0.02, -0.005], index=dates)
    benchmark_returns = pd.Series([0.005, -0.01, 0.015], index=dates)

    result = PerformanceMetrics().compute_benchmark(
        make_metrics(),
        strategy_returns,
        benchmark_returns,
    )

    expected_strategy_total = (1.01 * 1.02 * 0.995) - 1
    expected_benchmark_total = (1.005 * 0.99 * 1.015) - 1

    assert result.benchmark_return == pytest.approx(expected_benchmark_total, abs=1e-6)
    assert result.excess_return == pytest.approx(
        expected_strategy_total - expected_benchmark_total,
        abs=1e-6,
    )
    assert abs(result.benchmark_return) < 0.1


def make_trade(
    side: OrderSide,
    quantity: int,
    price: float,
    trade_date: date,
    commission: float = 0.0,
    stamp_tax: float = 0.0,
) -> Trade:
    return Trade(
        trade_id=f"T{side.value}{quantity}{trade_date}",
        order_id="O1",
        symbol="000001.SZ",
        side=side,
        quantity=quantity,
        price=price,
        amount=price * quantity,
        commission=commission,
        stamp_tax=stamp_tax,
        trade_time=datetime.combine(trade_date, datetime.min.time()),
        trade_date=trade_date,
    )


def test_pair_trades_handles_partial_sells_fifo():
    trades = [
        make_trade(OrderSide.BUY, 200, 10.0, date(2024, 1, 2), commission=2.0),
        make_trade(OrderSide.SELL, 100, 12.0, date(2024, 1, 3), commission=1.0, stamp_tax=1.0),
        make_trade(OrderSide.SELL, 100, 11.0, date(2024, 1, 4), commission=1.0, stamp_tax=1.0),
    ]

    completed = PerformanceMetrics._pair_trades(trades)

    assert len(completed) == 2
    assert [t.quantity for t in completed] == [100, 100]
    assert sum(t.pnl for t in completed) == pytest.approx(294.0)


def test_compute_exposes_fill_and_round_trip_counts():
    equity = pd.Series(
        [100_000.0, 101_000.0, 102_294.0],
        index=[date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
    )
    trades = [
        make_trade(OrderSide.BUY, 200, 10.0, date(2024, 1, 2), commission=2.0),
        make_trade(OrderSide.SELL, 100, 12.0, date(2024, 1, 3), commission=1.0, stamp_tax=1.0),
        make_trade(OrderSide.SELL, 100, 11.0, date(2024, 1, 4), commission=1.0, stamp_tax=1.0),
    ]

    result = PerformanceMetrics().compute(equity, trades)

    assert result.fill_count == 3
    assert result.round_trip_count == 2
    assert result.total_trades == 2
    assert result.realized_pnl == pytest.approx(294.0)


def test_compute_exposes_turnover_and_cost_drag():
    equity = pd.Series(
        [100_000.0, 101_000.0, 102_000.0],
        index=[date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
    )
    trades = [
        make_trade(OrderSide.BUY, 1000, 10.0, date(2024, 1, 3), commission=5.0),
        make_trade(OrderSide.SELL, 1000, 11.0, date(2024, 1, 4), commission=5.0, stamp_tax=5.5),
    ]

    result = PerformanceMetrics().compute(equity, trades)

    expected_daily = (0.0 + 10_000 / 101_000 + 11_000 / 102_000) / 3
    assert result.avg_daily_turnover == pytest.approx(expected_daily, abs=1e-6)
    assert result.annual_turnover == pytest.approx(expected_daily * 252, abs=1e-6)
    assert result.max_daily_turnover == pytest.approx(11_000 / 102_000, abs=1e-6)
    assert result.buy_turnover == pytest.approx(10_000 / 101_000, rel=1e-3)
    assert result.sell_turnover == pytest.approx(11_000 / 101_000, rel=1e-3)
    assert result.total_fees == pytest.approx(15.5)
    assert result.cost_drag == pytest.approx(15.5 / 100_000, abs=1e-6)
    assert result.gross_return > result.net_return


def test_compute_exposes_portfolio_exposure_metrics():
    equity = pd.Series(
        [100_000.0, 105_000.0, 110_000.0],
        index=[date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
    )
    account_history = {
        date(2024, 1, 2): AccountSnapshot(
            cash=40_000.0,
            positions={
                "AAA": PositionSnapshot(
                    symbol="AAA",
                    total_qty=100,
                    tradeable_qty=100,
                    avg_cost=200.0,
                    last_price=200.0,
                    market_value=20_000.0,
                    unrealized_pnl=0.0,
                ),
                "BBB": PositionSnapshot(
                    symbol="BBB",
                    total_qty=200,
                    tradeable_qty=200,
                    avg_cost=200.0,
                    last_price=200.0,
                    market_value=40_000.0,
                    unrealized_pnl=0.0,
                ),
            },
            total_assets=100_000.0,
            total_market_value=60_000.0,
        ),
        date(2024, 1, 3): AccountSnapshot(
            cash=20_000.0,
            positions={
                "AAA": PositionSnapshot(
                    symbol="AAA",
                    total_qty=150,
                    tradeable_qty=150,
                    avg_cost=200.0,
                    last_price=200.0,
                    market_value=30_000.0,
                    unrealized_pnl=0.0,
                ),
                "BBB": PositionSnapshot(
                    symbol="BBB",
                    total_qty=150,
                    tradeable_qty=150,
                    avg_cost=233.33,
                    last_price=233.33,
                    market_value=35_000.0,
                    unrealized_pnl=0.0,
                ),
                "CCC": PositionSnapshot(
                    symbol="CCC",
                    total_qty=100,
                    tradeable_qty=100,
                    avg_cost=200.0,
                    last_price=200.0,
                    market_value=20_000.0,
                    unrealized_pnl=0.0,
                ),
            },
            total_assets=105_000.0,
            total_market_value=85_000.0,
        ),
        date(2024, 1, 4): AccountSnapshot(
            cash=55_000.0,
            positions={
                "AAA": PositionSnapshot(
                    symbol="AAA",
                    total_qty=100,
                    tradeable_qty=100,
                    avg_cost=200.0,
                    last_price=250.0,
                    market_value=25_000.0,
                    unrealized_pnl=5_000.0,
                ),
                "BBB": PositionSnapshot(
                    symbol="BBB",
                    total_qty=120,
                    tradeable_qty=120,
                    avg_cost=250.0,
                    last_price=250.0,
                    market_value=30_000.0,
                    unrealized_pnl=0.0,
                ),
            },
            total_assets=110_000.0,
            total_market_value=55_000.0,
        ),
    }

    result = PerformanceMetrics().compute(
        equity,
        trades=[],
        account_history=account_history,
    )

    assert result.avg_position_count == pytest.approx((2 + 3 + 2) / 3, abs=1e-6)
    assert result.max_position_count == 3
    assert result.min_position_count == 2
    assert result.avg_cash_ratio == pytest.approx(
        (0.4 + (20_000 / 105_000) + 0.5) / 3,
        abs=1e-6,
    )
    assert result.average_exposure == pytest.approx(
        ((60_000 / 100_000) + (85_000 / 105_000) + (55_000 / 110_000)) / 3,
        abs=1e-6,
    )
    assert result.max_single_position_weight == pytest.approx(40_000 / 100_000, abs=1e-6)
    assert result.avg_top5_concentration == pytest.approx(result.average_exposure, abs=1e-6)
