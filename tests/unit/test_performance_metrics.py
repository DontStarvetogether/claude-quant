"""单元测试：绩效指标计算。"""

from datetime import date

import pandas as pd
import pytest

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
