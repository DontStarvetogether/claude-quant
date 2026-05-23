from __future__ import annotations

import json

import pandas as pd

from cq.benchmark import (
    BenchmarkResult,
    CrossValidationTolerance,
    compare_benchmark_with_external,
    export_cross_validation_result,
)


def _benchmark_result() -> BenchmarkResult:
    return BenchmarkResult(
        equity_curve=pd.DataFrame(
            [
                {"date": "2024-01-02", "total_assets": 1_000_000.0, "cash": 1_000_000.0, "position_value": 0.0},
                {"date": "2024-01-03", "total_assets": 1_010_000.0, "cash": 500_000.0, "position_value": 510_000.0},
            ]
        ),
        holdings=pd.DataFrame(
            [
                {"date": "2024-01-03", "symbol": "000001.SZ", "quantity": 10_000, "close": 10.2, "market_value": 102_000.0, "weight": 0.1},
                {"date": "2024-01-03", "symbol": "600000.SH", "quantity": 20_000, "close": 20.4, "market_value": 408_000.0, "weight": 0.4},
            ]
        ),
        trades=pd.DataFrame(
            [
                {"trade_date": "2024-01-03", "symbol": "000001.SZ", "side": "BUY", "quantity": 10_000, "price": 10.0, "amount": 100_000.0, "commission": 30.0, "stamp_tax": 0.0, "net_amount": 100_030.0},
                {"trade_date": "2024-01-03", "symbol": "600000.SH", "side": "BUY", "quantity": 20_000, "price": 20.0, "amount": 400_000.0, "commission": 120.0, "stamp_tax": 0.0, "net_amount": 400_120.0},
            ]
        ),
        signals=pd.DataFrame(),
    )


def test_cross_validation_passes_matching_external_frames():
    result = _benchmark_result()
    external = {
        "equity_curve": result.equity_curve.copy(),
        "holdings": result.holdings.copy(),
        "trades": result.trades.rename(columns={"trade_date": "date"}).copy(),
    }

    comparison = compare_benchmark_with_external(result, external, platform_name="JoinQuant")

    assert comparison.summary["schema_version"] == "cross_validation.v1"
    assert comparison.summary["platform_name"] == "JoinQuant"
    assert comparison.summary["passed"] is True
    assert comparison.summary["total_mismatches"] == 0
    assert set(comparison.equity["status"]) == {"matched"}
    assert comparison.markdown.startswith("# 平台交叉验证报告")
    assert "| 结果 | PASS |" in comparison.markdown


def test_cross_validation_detects_value_differences_and_missing_rows():
    result = _benchmark_result()
    external = {
        "equity_curve": result.equity_curve.assign(
            total_assets=[1_000_000.0, 1_010_050.0]
        ),
        "holdings": result.holdings.iloc[[0]].copy(),
        "trades": result.trades.iloc[[0]].assign(price=[10.2], amount=[102_000.0]).copy(),
    }

    comparison = compare_benchmark_with_external(
        result,
        external,
        CrossValidationTolerance(equity_abs=1.0, amount_abs=1.0, price_abs=0.01),
    )

    assert comparison.summary["passed"] is False
    assert comparison.summary["equity_mismatches"] == 1
    assert comparison.summary["holding_mismatches"] == 1
    assert comparison.summary["trade_mismatches"] == 2
    assert "different" in set(comparison.trades["status"])
    assert "missing_external" in set(comparison.trades["status"])
    assert "| 结果 | FAIL |" in comparison.markdown


def test_export_cross_validation_result_writes_standard_files(tmp_path):
    result = _benchmark_result()
    comparison = compare_benchmark_with_external(
        result,
        {
            "equity_curve": result.equity_curve.copy(),
            "holdings": result.holdings.copy(),
            "trades": result.trades.copy(),
        },
    )

    exported = export_cross_validation_result(comparison, tmp_path)

    assert set(exported.files) == {
        "equity_comparison",
        "holdings_comparison",
        "trades_comparison",
        "summary",
        "report",
    }
    for path in exported.files.values():
        assert path.exists()

    payload = json.loads(exported.files["summary"].read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["schema_version"] == "cross_validation.v1"
    assert exported.files["report"].read_text(encoding="utf-8").startswith("# 平台交叉验证报告")
