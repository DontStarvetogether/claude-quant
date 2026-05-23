from __future__ import annotations

import json

import pandas as pd

from cq.benchmark import (
    MomentumTopNConfig,
    export_benchmark_result,
    generate_benchmark_report,
    run_momentum_topn_benchmark,
    summarize_benchmark_result,
)


def _price_frame(closes: dict[str, list[float]]) -> pd.DataFrame:
    dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    rows = []
    for symbol, values in closes.items():
        for trade_date, close in zip(dates, values, strict=True):
            rows.append({"date": trade_date, "symbol": symbol, "open": close, "close": close})
    return pd.DataFrame(rows)


def _benchmark_result():
    prices = _price_frame(
        {
            "000001.SZ": [10, 11, 13, 14, 15],
            "000002.SZ": [10, 10.5, 12, 11, 10],
            "000003.SZ": [10, 10, 10.5, 14, 16],
        }
    )
    return run_momentum_topn_benchmark(
        prices,
        MomentumTopNConfig(lookback=2, top_n=2, rebalance="D", initial_capital=1_000_000),
    )


def test_summarize_benchmark_result_outputs_core_metrics():
    result = _benchmark_result()

    summary = summarize_benchmark_result(result)

    assert summary.start_date == "2024-01-01"
    assert summary.end_date == "2024-01-05"
    assert summary.trading_days == 5
    assert summary.initial_assets == 1_000_000.0
    assert summary.trade_count == len(result.trades)
    assert summary.buy_count > 0
    assert summary.total_fees == round(summary.total_commission + summary.total_stamp_tax, 2)
    assert summary.final_cash == result.equity_curve["cash"].iloc[-1]
    assert summary.final_position_value == result.equity_curve["position_value"].iloc[-1]


def test_generate_benchmark_report_contains_metrics_and_field_mapping():
    result = _benchmark_result()

    report = generate_benchmark_report(
        result,
        name="20日动量 Top2",
        universe="TEST_POOL",
        metadata={"调仓频率": "日频"},
    )

    assert report.markdown.startswith("# Benchmark 报告：20日动量 Top2")
    assert "## 测试范围" in report.markdown
    assert "| 股票池 | TEST_POOL |" in report.markdown
    assert "| 调仓频率 | 日频 |" in report.markdown
    assert "## 核心指标" in report.markdown
    assert "## 成交概览" in report.markdown
    assert "## 回测页面字段映射" in report.markdown
    assert "| equity_curve.total_assets | equity_curve.values |" in report.markdown
    assert report.markdown.endswith("\n")


def test_generate_benchmark_report_renders_universe_source_metadata():
    result = _benchmark_result()

    report = generate_benchmark_report(
        result,
        universe="HS300_PIT",
        metadata={
            "universe_source": {
                "provider": "akshare",
                "source_quality": "free_best_effort_latest_snapshot",
                "strict_historical_pit": False,
                "effective_coverage_start": "2026-04-30",
                "snapshot_dates": {"hs300_pit": "2026-04-30"},
            },
            "universe_quality_warning": "PIT universe source is best-effort/latest snapshot.",
        },
    )

    assert "| 股票池数据源 | akshare |" in report.markdown
    assert "| 股票池数据质量 | free_best_effort_latest_snapshot |" in report.markdown
    assert "| 严格历史 PIT | 否 |" in report.markdown
    assert "| 有效覆盖起点 | 2026-04-30 |" in report.markdown
    assert "| 股票池质量警告 | PIT universe source is best-effort/latest snapshot. |" in report.markdown


def test_export_benchmark_result_writes_standard_files(tmp_path):
    result = _benchmark_result()

    exported = export_benchmark_result(
        result,
        tmp_path,
        name="20日动量 Top2",
        universe="TEST_POOL",
        metadata={"lookback": 2, "top_n": 2},
    )

    expected = {"equity_curve", "holdings", "trades", "signals", "summary", "report"}
    assert set(exported.files) == expected
    for path in exported.files.values():
        assert path.exists()

    equity = pd.read_csv(exported.files["equity_curve"])
    trades = pd.read_csv(exported.files["trades"])
    assert equity["date"].iloc[0] == "2024-01-01"
    assert "total_assets" in equity.columns
    assert "net_amount" in trades.columns

    payload = json.loads(exported.files["summary"].read_text(encoding="utf-8"))
    assert payload["schema_version"] == "benchmark.v1"
    assert payload["name"] == "20日动量 Top2"
    assert payload["universe"] == "TEST_POOL"
    assert payload["metadata"] == {"lookback": 2, "top_n": 2}
    assert payload["summary"]["trade_count"] == len(result.trades)
    assert payload["backtest_field_mapping"]["summary.total_return"] == "metrics.total_return"
    assert payload["files"]["equity_curve"] == "equity_curve.csv"
    assert exported.files["report"].read_text(encoding="utf-8").startswith("# Benchmark 报告")
