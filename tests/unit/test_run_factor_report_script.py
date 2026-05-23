from __future__ import annotations

import json

import pandas as pd
from click.testing import CliRunner

from scripts.run_factor_report import main


def test_run_factor_report_script_generates_standard_export(tmp_path):
    factor_csv = tmp_path / "factor.csv"
    price_csv = tmp_path / "price.csv"
    output_dir = tmp_path / "report"

    pd.DataFrame(
        {
            "date": ["2024-01-01"] * 5 + ["2024-01-02"] * 5,
            "symbol": ["A", "B", "C", "D", "E"] * 2,
            "factor": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
        }
    ).to_csv(factor_csv, index=False)
    pd.DataFrame(
        {
            "date": ["2024-01-01"] * 5 + ["2024-01-02"] * 5,
            "symbol": ["A", "B", "C", "D", "E"] * 2,
            "close": [10, 10, 10, 10, 10, 11, 12, 13, 14, 15],
        }
    ).to_csv(price_csv, index=False)

    result = CliRunner().invoke(
        main,
        [
            "--factor-csv",
            str(factor_csv),
            "--price-csv",
            str(price_csv),
            "--output-dir",
            str(output_dir),
            "--factor-name",
            "demo_factor",
            "--period",
            "1",
            "--groups",
            "5",
            "--universe",
            "TEST",
            "--metadata",
            "rebalance=D",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["factor_name"] == "demo_factor"
    assert summary["universe"] == "TEST"
    assert summary["metadata"] == {"rebalance": "D"}
    assert (output_dir / "report.md").read_text(encoding="utf-8").startswith("# 因子报告")
    assert (output_dir / "ic_summary.csv").exists()


def test_run_factor_report_script_rejects_bad_metadata(tmp_path):
    factor_csv = tmp_path / "factor.csv"
    price_csv = tmp_path / "price.csv"
    pd.DataFrame({"date": ["2024-01-01"], "symbol": ["A"], "factor": [1]}).to_csv(
        factor_csv, index=False
    )
    pd.DataFrame({"date": ["2024-01-01"], "symbol": ["A"], "close": [10]}).to_csv(
        price_csv, index=False
    )

    result = CliRunner().invoke(
        main,
        [
            "--factor-csv",
            str(factor_csv),
            "--price-csv",
            str(price_csv),
            "--output-dir",
            str(tmp_path / "report"),
            "--metadata",
            "bad",
        ],
    )

    assert result.exit_code != 0
    assert "metadata must be key=value" in result.output
