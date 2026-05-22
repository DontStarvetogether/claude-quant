"""单元测试：回测结果序列化。"""

from datetime import date, datetime

import pandas as pd

from cq.engine.backtest_engine import BacktestResult
from cq.performance.metrics import MetricsResult
from web.serializers import serialize_result
from web.store import RunRecord


def _metrics() -> MetricsResult:
    return MetricsResult(
        total_return=0.01,
        annual_return=0.02,
        max_drawdown=-0.01,
        volatility=0.1,
        sharpe_ratio=0.5,
        sortino_ratio=0.6,
        calmar_ratio=0.7,
        total_trades=0,
        win_rate=0.0,
        avg_profit=0.0,
        avg_loss=0.0,
        profit_factor=float("inf"),
        avg_hold_days=0.0,
        total_fees=0.0,
        final_value=1010000.0,
        initial_value=1000000.0,
        max_drawdown_start=None,
        max_drawdown_end=None,
    )


def test_serialize_result_exposes_benchmark_unavailable_reason():
    result = BacktestResult(
        strategy_name="double_ma",
        symbols=["600519.SH"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 3),
        initial_capital=1_000_000,
        metrics=_metrics(),
        equity_curve=pd.Series(
            [1_000_000.0, 1_010_000.0],
            index=[date(2024, 1, 2), date(2024, 1, 3)],
        ),
        trades=[],
        rejected_orders=[],
        benchmark="000300.SH",
        benchmark_status="unavailable",
        benchmark_error="基准数据为空",
        alpha_beta_available=False,
    )
    record = RunRecord(
        run_id="run-1",
        strategy_name="double_ma",
        symbols=["600519.SH"],
        start_date="2024-01-01",
        end_date="2024-01-03",
        initial_capital=1_000_000,
        status="completed",
        result=result,
        created_at=datetime(2024, 1, 4),
    )

    payload = serialize_result(record)

    assert payload.benchmark_status == "unavailable"
    assert payload.benchmark_error == "基准数据为空"
    assert payload.alpha_beta_available is False
    assert payload.benchmark_curve_available is False
    assert payload.benchmark_name == "沪深300"
    assert payload.metrics.profit_factor is None


def test_serialize_result_exposes_benchmark_diagnostics():
    result = BacktestResult(
        strategy_name="double_ma",
        symbols=["600519.SH"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 4),
        initial_capital=1_000_000,
        metrics=_metrics(),
        equity_curve=pd.Series(
            [1_000_000.0, 1_010_000.0, 1_020_000.0],
            index=[date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        ),
        trades=[],
        rejected_orders=[],
        benchmark="000300.SH",
        benchmark_curve=pd.Series(
            [1_000_000.0, 1_005_000.0, 1_012_000.0],
            index=[date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        ),
        benchmark_status="available",
        alpha_beta_available=True,
        benchmark_diagnostics={
            "sample_days": 2,
            "missing_days": 0,
            "win_days": 2,
            "hit_rate": 1.0,
            "avg_daily_excess": 0.002,
            "relative_return": 0.007905,
            "common_start": "2024-01-02",
            "common_end": "2024-01-04",
            "aligned": True,
        },
    )
    record = RunRecord(
        run_id="run-2",
        strategy_name="double_ma",
        symbols=["600519.SH"],
        start_date="2024-01-01",
        end_date="2024-01-04",
        initial_capital=1_000_000,
        status="completed",
        result=result,
        created_at=datetime(2024, 1, 5),
    )

    payload = serialize_result(record)

    assert payload.benchmark_diagnostics is not None
    assert payload.benchmark_diagnostics.sample_days == 2
    assert payload.benchmark_diagnostics.win_days == 2
    assert payload.benchmark_diagnostics.aligned is True
