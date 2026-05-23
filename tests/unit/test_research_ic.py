from __future__ import annotations

import math

import pandas as pd
import pytest

from cq.research import calculate_ic, summarize_ic


def test_calculate_rank_ic_and_summary():
    data = pd.DataFrame(
        {
            "date": ["2024-01-01"] * 3 + ["2024-01-02"] * 3,
            "symbol": ["A", "B", "C"] * 2,
            "factor": [1.0, 2.0, 3.0, 1.0, 2.0, 3.0],
            "forward_return_1d": [0.01, 0.02, 0.03, 0.03, 0.02, 0.01],
        }
    )

    ic = calculate_ic(data, periods=[1])
    summary = summarize_ic(ic)

    assert ic["ic"].tolist() == pytest.approx([1.0, -1.0])
    assert summary.loc[0, "period"] == 1
    assert summary.loc[0, "ic_mean"] == pytest.approx(0.0)
    assert summary.loc[0, "ic_win_rate"] == pytest.approx(0.5)
    assert summary.loc[0, "count"] == 2


def test_calculate_ic_returns_nan_without_cross_sectional_variation():
    data = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-01"],
            "symbol": ["A", "B"],
            "factor": [1.0, 1.0],
            "forward_return_1d": [0.01, 0.02],
        }
    )

    ic = calculate_ic(data, periods=[1])

    assert math.isnan(ic.loc[0, "ic"])
    assert ic.loc[0, "sample_count"] == 2


def test_calculate_ic_validates_method():
    data = pd.DataFrame({"date": [], "factor": [], "forward_return_1d": []})

    with pytest.raises(ValueError, match="method"):
        calculate_ic(data, periods=[1], method="bad")
