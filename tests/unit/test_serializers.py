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


def test_serialize_result_exposes_data_diagnostics():
    result = BacktestResult(
        strategy_name="double_ma",
        symbols=["600519.SH"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 4),
        initial_capital=1_000_000,
        metrics=_metrics(),
        equity_curve=pd.Series(
            [1_000_000.0, 1_010_000.0],
            index=[date(2024, 1, 2), date(2024, 1, 3)],
        ),
        trades=[],
        rejected_orders=[],
        benchmark="000300.SH",
        data_diagnostics={
            "symbols": [
                {
                    "symbol": "600519.SH",
                    "role": "trade_symbol",
                    "status": "updated",
                    "new_records": 2,
                    "used_cache": True,
                    "local_first_date": "2024-01-02",
                    "local_last_date": "2024-01-03",
                    "requested_start": "2024-01-01",
                    "requested_end": "2024-01-04",
                    "source": "FakeSource",
                    "cache_updated_at": "2024-01-04T10:00:00",
                    "qfq_first_date": "2024-01-02",
                    "qfq_last_date": "2024-01-03",
                    "factor_first_date": "2024-01-02",
                    "factor_last_date": "2024-01-03",
                    "qfq_available": True,
                    "factor_available": True,
                    "st_status_source": "unavailable",
                    "limit_price_source": "exchange_or_calculated",
                    "repair_actions": ["recalculate_qfq"],
                    "quality_level": "pass",
                    "error": None,
                }
            ],
            "benchmark": {
                "symbol": "000300.SH",
                "role": "benchmark",
                "status": "download_failed_cache_available",
                "new_records": 0,
                "used_cache": True,
                "local_first_date": "2024-01-02",
                "local_last_date": "2024-01-03",
                "requested_start": "2024-01-01",
                "requested_end": "2024-01-04",
                "error": "network down",
            },
            "summary": {
                "total": 2,
                "updated": 1,
                "cache_hit": 0,
                "failed": 1,
                "missing": 0,
            },
        },
    )
    record = RunRecord(
        run_id="run-3",
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

    assert payload.data_diagnostics is not None
    assert payload.data_diagnostics.summary.total == 2
    assert payload.data_diagnostics.symbols[0].status == "updated"
    assert payload.data_diagnostics.symbols[0].source == "FakeSource"
    assert payload.data_diagnostics.symbols[0].repair_actions == ["recalculate_qfq"]
    assert payload.data_diagnostics.symbols[0].quality_level == "pass"
    assert payload.data_diagnostics.benchmark is not None
    assert payload.data_diagnostics.benchmark.error == "network down"


def test_serialize_result_exposes_universe_diagnostics():
    result = BacktestResult(
        strategy_name="trend_rank",
        symbols=["600519.SH", "000858.SZ"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 4),
        initial_capital=1_000_000,
        metrics=_metrics(),
        equity_curve=pd.Series(
            [1_000_000.0, 1_010_000.0],
            index=[date(2024, 1, 2), date(2024, 1, 3)],
        ),
        trades=[],
        rejected_orders=[],
        universe_diagnostics={
            "universe_id": "custom_static",
            "universe_name": "自定义静态股票池",
            "source": "user_selection",
            "construction": "static",
            "selection_time": "run_submit",
            "symbol_count": 2,
            "survivorship_bias_risk": "medium",
            "universe_type": "static_builtin",
            "point_in_time_available": False,
            "history_membership_available": False,
            "point_in_time": False,
            "warnings": ["static_universe_survivorship_bias"],
            "notes": ["多标的静态股票池未记录历史成分，存在幸存者偏差风险。"],
        },
    )
    record = RunRecord(
        run_id="run-4",
        strategy_name="trend_rank",
        symbols=["600519.SH", "000858.SZ"],
        start_date="2024-01-01",
        end_date="2024-01-04",
        initial_capital=1_000_000,
        status="completed",
        result=result,
        created_at=datetime(2024, 1, 5),
    )

    payload = serialize_result(record)

    assert payload.universe_diagnostics is not None
    assert payload.universe_diagnostics.construction == "static"
    assert payload.universe_diagnostics.universe_type == "static_builtin"
    assert payload.universe_diagnostics.point_in_time is False
    assert payload.universe_diagnostics.survivorship_bias_risk == "medium"


def test_serialize_result_exposes_execution_and_metric_diagnostics():
    result = BacktestResult(
        strategy_name="double_ma",
        symbols=["600519.SH"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 4),
        initial_capital=1_000_000,
        metrics=_metrics(),
        equity_curve=pd.Series(
            [1_000_000.0, 1_010_000.0],
            index=[date(2024, 1, 2), date(2024, 1, 3)],
        ),
        trades=[],
        rejected_orders=[],
        execution_assumptions={
            "execution_model": "next_open",
            "signal_timing": "D 日收盘后产生信号",
            "fill_timing": "D+1 交易日开盘价撮合",
            "uses_intraday_touch": False,
        },
        execution_diagnostics={
            "filled_count": 0,
            "rejected_count": 0,
            "partial_fill_count": 0,
        },
        metric_diagnostics={
            "quality_level": "warning",
            "sample_days": 1,
            "round_trip_count": 0,
            "win_rate_basis": "completed_round_trips_fifo",
            "warnings": ["sample_days_too_few"],
        },
    )
    record = RunRecord(
        run_id="run-5",
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

    assert payload.execution_assumptions is not None
    assert payload.execution_assumptions["uses_intraday_touch"] is False
    assert payload.execution_diagnostics is not None
    assert payload.execution_diagnostics["partial_fill_count"] == 0
    assert payload.metric_diagnostics is not None
    assert payload.metric_diagnostics["win_rate_basis"] == "completed_round_trips_fifo"
