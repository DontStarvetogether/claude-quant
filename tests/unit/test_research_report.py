from __future__ import annotations

import pandas as pd
import pytest

from cq.research import (
    analyze_factor_groups,
    calculate_ic,
    generate_factor_report,
    summarize_ic,
)


def test_generate_factor_report_contains_core_sections_and_metrics():
    factor_return = pd.DataFrame(
        {
            "date": ["2024-01-01"] * 5 + ["2024-01-02"] * 5,
            "symbol": ["A", "B", "C", "D", "E"] * 2,
            "factor": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
            "forward_return_1d": [0.01, 0.02, 0.03, 0.04, 0.05, 0.02, 0.03, 0.04, 0.05, 0.06],
        }
    )
    analysis = analyze_factor_groups(factor_return, group_count=5, periods=[1])
    ic_summary = summarize_ic(calculate_ic(factor_return, periods=[1]))

    report = generate_factor_report(
        factor_name="20日动量",
        universe="HS300_STATIC",
        start_date="2024-01-01",
        end_date="2024-01-02",
        metadata={"调仓频率": "日频"},
        analysis=analysis,
        ic_summary=ic_summary,
    )

    markdown = report.markdown
    assert markdown.startswith("# 因子报告：20日动量")
    assert "## 测试范围" in markdown
    assert "| 股票池 | HS300_STATIC |" in markdown
    assert "| 调仓频率 | 日频 |" in markdown
    assert "## 覆盖率" in markdown
    assert "| 平均覆盖率 | 100.00% |" in markdown
    assert "## IC 分析" in markdown
    assert "| 1D | 1.0000" in markdown
    assert "## 分层收益" in markdown
    assert "| 1D | 5 | 5.50%" in markdown
    assert "## Top-Bottom" in markdown
    assert "| 1D | 4.00%" in markdown
    assert "## 单调性" in markdown
    assert "## 分组换手" in markdown
    assert markdown.endswith("\n")


def test_generate_factor_report_handles_empty_tables_with_placeholders():
    empty_analysis = analyze_factor_groups(
        pd.DataFrame(columns=["date", "symbol", "factor", "forward_return_1d"]),
        group_count=5,
        periods=[1],
    )
    empty_ic_summary = summarize_ic(pd.DataFrame(columns=["date", "period", "ic", "sample_count"]))

    report = generate_factor_report(
        factor_name="空因子",
        analysis=empty_analysis,
        ic_summary=empty_ic_summary,
    )

    assert "| — | — |" in report.markdown
    assert "| 样本日期数 | 0 |" in report.markdown


def test_generate_factor_report_validates_required_columns():
    analysis = analyze_factor_groups(
        pd.DataFrame(columns=["date", "symbol", "factor", "forward_return_1d"]),
        group_count=5,
        periods=[1],
    )

    with pytest.raises(ValueError, match="missing required columns"):
        generate_factor_report(
            factor_name="bad",
            analysis=analysis,
            ic_summary=pd.DataFrame(columns=["period"]),
        )
