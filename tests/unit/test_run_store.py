"""单元测试：回测运行记录持久化。"""

from datetime import date

import pandas as pd

from cq.engine.backtest_engine import BacktestResult
from cq.performance.metrics import MetricsResult
from web.store import RunStore


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
        profit_factor=0.0,
        avg_hold_days=0.0,
        total_fees=0.0,
        final_value=1010000.0,
        initial_value=1000000.0,
        max_drawdown_start=None,
        max_drawdown_end=None,
    )


def test_run_store_persists_status_across_instances(tmp_path):
    db_path = tmp_path / "backtest.db"
    store = RunStore(db_path)
    record = store.create(
        strategy_name="double_ma",
        symbols=["600519.SH"],
        start_date="2024-01-01",
        end_date="2024-02-01",
        initial_capital=1_000_000,
        benchmark="000300.SH",
    )

    store.update_status(
        record.run_id,
        status="running",
        progress=30,
        current_date="2024-01-15",
        total_assets=1_001_000,
        elapsed_seconds=1.2,
    )

    loaded = RunStore(db_path).get(record.run_id)

    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.progress == 30
    assert loaded.current_date == "2024-01-15"
    assert loaded.benchmark == "000300.SH"


def test_run_store_persists_completed_result(tmp_path):
    db_path = tmp_path / "backtest.db"
    store = RunStore(db_path)
    record = store.create(
        strategy_name="double_ma",
        symbols=["600519.SH"],
        start_date="2024-01-01",
        end_date="2024-01-03",
        initial_capital=1_000_000,
    )
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
        benchmark_error="样本不足",
    )

    store.save_result(record.run_id, result, elapsed_seconds=2.5)
    loaded = RunStore(db_path).get(record.run_id)

    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.progress == 100
    assert loaded.result is not None
    assert loaded.result.benchmark_error == "样本不足"
