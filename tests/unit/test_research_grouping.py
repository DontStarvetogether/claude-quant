from __future__ import annotations

import math

import pandas as pd
import pytest

from cq.research import analyze_factor_groups


def test_analyze_factor_groups_outputs_returns_nav_and_top_bottom():
    factor = pd.DataFrame(
        {
            "date": ["2024-01-01"] * 5 + ["2024-01-02"] * 5,
            "symbol": ["A", "B", "C", "D", "E"] * 2,
            "factor": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
        }
    )
    forward = pd.DataFrame(
        {
            "date": ["2024-01-01"] * 5 + ["2024-01-02"] * 5,
            "symbol": ["A", "B", "C", "D", "E"] * 2,
            "forward_return_1d": [0.01, 0.02, 0.03, 0.04, 0.05, 0.02, 0.03, 0.04, 0.05, 0.06],
        }
    )

    result = analyze_factor_groups(factor, forward, group_count=5, periods=[1])

    first_top_bottom = result.top_bottom_return.loc[
        result.top_bottom_return["date"] == "2024-01-01",
        "top_bottom_return",
    ].iloc[0]
    group5_nav = result.group_nav[
        (result.group_nav["period"] == 1) & (result.group_nav["group"] == 5)
    ].sort_values("date")["nav"].tolist()

    assert len(result.group_return) == 10
    assert first_top_bottom == pytest.approx(0.04)
    assert group5_nav == pytest.approx([1.05, 1.05 * 1.06])
    assert result.monotonicity.loc[0, "mean_group_rank_corr"] == pytest.approx(1.0)
    assert result.monotonicity.loc[0, "monotonic_ratio"] == pytest.approx(1.0)


def test_analyze_factor_groups_reports_coverage_and_turnover():
    factor = pd.DataFrame(
        {
            "date": ["2024-01-01"] * 4 + ["2024-01-02"] * 4,
            "symbol": ["A", "B", "C", "D", "A", "B", "C", "D"],
            "factor": [1.0, 2.0, 3.0, None, 4.0, 1.0, 3.0, 2.0],
            "forward_return_1d": [0.01, 0.02, 0.03, 0.04, 0.02, 0.01, 0.03, 0.04],
        }
    )

    result = analyze_factor_groups(factor, group_count=2, periods=[1])

    coverage = result.coverage.set_index("date")
    second_day_group1_turnover = result.turnover_by_group[
        (result.turnover_by_group["date"] == "2024-01-02")
        & (result.turnover_by_group["group"] == 1)
    ]["turnover"].iloc[0]

    assert coverage.loc["2024-01-01", "coverage"] == pytest.approx(0.75)
    assert coverage.loc["2024-01-02", "coverage"] == pytest.approx(1.0)
    assert math.isnan(
        result.turnover_by_group[result.turnover_by_group["date"] == "2024-01-01"][
            "turnover"
        ].iloc[0]
    )
    assert second_day_group1_turnover == pytest.approx(0.5)


def test_analyze_factor_groups_validates_group_count():
    data = pd.DataFrame({"date": [], "symbol": [], "factor": [], "forward_return_1d": []})

    with pytest.raises(ValueError, match="group_count"):
        analyze_factor_groups(data, group_count=1, periods=[1])
