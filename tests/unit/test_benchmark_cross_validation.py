from __future__ import annotations

import json

import pandas as pd
from click.testing import CliRunner

from cq.benchmark import (
    BenchmarkResult,
    CrossValidationInputFiles,
    CrossValidationTolerance,
    compare_benchmark_with_external,
    export_cross_validation_result,
    export_cross_validation_template,
    load_cross_validation_frames,
)
from scripts.run_cross_validation import main as run_cross_validation_main


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


def test_export_cross_validation_template_writes_required_contract_files(tmp_path):
    exported = export_cross_validation_template(tmp_path, platform_name="JoinQuant")

    assert set(exported.files) == {
        "equity_curve",
        "holdings",
        "trades",
        "assumptions",
        "readme",
    }
    assert pd.read_csv(exported.files["equity_curve"]).columns.tolist() == [
        "date",
        "total_assets",
        "cash",
        "position_value",
    ]
    assumptions = json.loads(exported.files["assumptions"].read_text(encoding="utf-8"))
    assert assumptions["schema_version"] == "cross_validation_template.v1"
    assert assumptions["platform_name"] == "JoinQuant"
    assert "adjustment_mode" in assumptions["must_record"]


def test_load_cross_validation_frames_standardizes_common_platform_aliases(tmp_path):
    equity_csv = tmp_path / "equity.csv"
    holdings_csv = tmp_path / "holdings.csv"
    trades_csv = tmp_path / "trades.csv"

    pd.DataFrame(
        [
            {
                "交易日期": "2024-01-03",
                "总资产": 1_010_000.0,
                "可用资金": 500_000.0,
                "持仓市值": 510_000.0,
            }
        ]
    ).to_csv(equity_csv, index=False)
    pd.DataFrame(
        [
            {
                "日期": "2024-01-03",
                "证券代码": "000001.sz",
                "持仓数量": 10_000,
                "市值": 102_000.0,
            }
        ]
    ).to_csv(holdings_csv, index=False)
    pd.DataFrame(
        [
            {
                "成交日期": "2024-01-03",
                "证券代码": "000001.sz",
                "买卖方向": "买入",
                "成交数量": 10_000,
                "成交价格": 10.0,
                "手续费": 30.0,
            }
        ]
    ).to_csv(trades_csv, index=False)

    frames = load_cross_validation_frames(
        CrossValidationInputFiles(
            equity_curve=equity_csv,
            holdings=holdings_csv,
            trades=trades_csv,
        ),
        source_name="JoinQuant",
    )

    assert list(frames["equity_curve"].columns) == ["date", "total_assets", "cash", "position_value"]
    assert list(frames["holdings"].columns) == ["date", "symbol", "quantity", "market_value"]
    assert list(frames["trades"].columns) == [
        "trade_date",
        "symbol",
        "side",
        "quantity",
        "price",
        "amount",
        "commission",
        "stamp_tax",
        "net_amount",
    ]
    trade = frames["trades"].iloc[0]
    assert trade["side"] == "BUY"
    assert trade["amount"] == 100_000.0
    assert trade["net_amount"] == 100_030.0


def test_run_cross_validation_script_compares_export_directories(tmp_path):
    result = _benchmark_result()
    local_dir = tmp_path / "local"
    external_dir = tmp_path / "external"
    output_dir = tmp_path / "comparison"
    local_dir.mkdir()
    external_dir.mkdir()

    result.equity_curve.to_csv(local_dir / "equity_curve.csv", index=False)
    result.holdings.to_csv(local_dir / "holdings.csv", index=False)
    result.trades.to_csv(local_dir / "trades.csv", index=False)

    result.equity_curve.rename(
        columns={
            "date": "交易日期",
            "total_assets": "总资产",
            "cash": "可用资金",
            "position_value": "持仓市值",
        }
    ).to_csv(external_dir / "equity_curve.csv", index=False)
    result.holdings.rename(
        columns={
            "date": "日期",
            "symbol": "证券代码",
            "quantity": "持仓数量",
            "market_value": "市值",
        }
    )[["日期", "证券代码", "持仓数量", "市值"]].to_csv(external_dir / "holdings.csv", index=False)
    result.trades.rename(
        columns={
            "trade_date": "成交日期",
            "symbol": "证券代码",
            "side": "买卖方向",
            "quantity": "成交数量",
            "price": "成交价格",
            "amount": "成交金额",
            "commission": "手续费",
            "stamp_tax": "印花税",
            "net_amount": "净成交金额",
        }
    ).replace({"买卖方向": {"BUY": "买入", "SELL": "卖出"}}).to_csv(
        external_dir / "trades.csv",
        index=False,
    )

    cli_result = CliRunner().invoke(
        run_cross_validation_main,
        [
            "--local-dir",
            str(local_dir),
            "--external-dir",
            str(external_dir),
            "--output-dir",
            str(output_dir),
            "--platform-name",
            "JoinQuant",
        ],
    )

    assert cli_result.exit_code == 0, cli_result.output
    summary = json.loads((output_dir / "cross_validation_summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] is True
    assert summary["platform_name"] == "JoinQuant"
    assert (output_dir / "cross_validation_report.md").exists()
