from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from cq.data.feed.historical import HistoricalFeed


@pytest.mark.parametrize(
    ("symbol", "is_st", "expected_up", "expected_down"),
    [
        ("600519.SH", False, 11.0, 9.0),
        ("600001.SH", True, 10.5, 9.5),
        ("300750.SZ", False, 12.0, 8.0),
        ("688001.SH", False, 12.0, 8.0),
        ("430047.BJ", False, 13.0, 7.0),
    ],
)
def test_row_to_bar_uses_astock_rules_for_limit_price_fallback(
    symbol: str,
    is_st: bool,
    expected_up: float,
    expected_down: float,
):
    row = pd.Series(
        {
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.2,
            "pre_close": 10.0,
            "volume": 1000,
            "amount": 10_000.0,
            "is_st": is_st,
        }
    )

    bar = HistoricalFeed._row_to_bar(row, symbol, date(2024, 1, 2))

    assert bar.limit_up == expected_up
    assert bar.limit_down == expected_down


def test_row_to_bar_recalculates_nan_limit_price_from_pre_close():
    row = pd.Series(
        {
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.2,
            "pre_close": 10.0,
            "limit_up": float("nan"),
            "limit_down": float("nan"),
            "volume": 1000,
            "amount": 10_000.0,
            "is_st": False,
        }
    )

    bar = HistoricalFeed._row_to_bar(row, "300750.SZ", date(2024, 1, 2))

    assert bar.limit_up == 12.0
    assert bar.limit_down == 8.0
