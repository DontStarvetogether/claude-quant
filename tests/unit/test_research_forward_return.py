from __future__ import annotations

import math

import pandas as pd
import pytest

from cq.research import calculate_forward_returns


def test_calculate_forward_returns_groups_by_symbol():
    prices = pd.DataFrame(
        {
            "date": [
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
            ],
            "symbol": ["AAA", "AAA", "AAA", "BBB", "BBB", "BBB"],
            "close": [10.0, 11.0, 12.1, 20.0, 18.0, 19.8],
        }
    )

    result = calculate_forward_returns(prices, periods=[1, 2])

    aaa = result[result["symbol"] == "AAA"].reset_index(drop=True)
    bbb = result[result["symbol"] == "BBB"].reset_index(drop=True)
    assert aaa.loc[0, "forward_return_1d"] == pytest.approx(0.10)
    assert aaa.loc[0, "forward_return_2d"] == pytest.approx(0.21)
    assert bbb.loc[0, "forward_return_1d"] == pytest.approx(-0.10)
    assert bbb.loc[1, "forward_return_1d"] == pytest.approx(0.10)
    assert math.isnan(aaa.loc[2, "forward_return_1d"])


def test_calculate_forward_returns_zero_price_becomes_nan():
    prices = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02"],
            "symbol": ["AAA", "AAA"],
            "close": [0.0, 10.0],
        }
    )

    result = calculate_forward_returns(prices, periods=[1])

    assert math.isnan(result.loc[0, "forward_return_1d"])


def test_calculate_forward_returns_validates_columns_and_periods():
    prices = pd.DataFrame({"date": ["2024-01-01"], "symbol": ["AAA"]})

    with pytest.raises(ValueError, match="missing required columns"):
        calculate_forward_returns(prices)

    with pytest.raises(ValueError, match="positive integers"):
        calculate_forward_returns(pd.DataFrame({"date": [], "symbol": [], "close": []}), periods=[0])
