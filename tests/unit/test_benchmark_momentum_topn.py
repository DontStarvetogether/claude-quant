from __future__ import annotations

import pandas as pd
import pytest

from cq.benchmark import MomentumTopNConfig, run_momentum_topn_benchmark


def _price_frame(closes: dict[str, list[float]], dates: list[str] | None = None) -> pd.DataFrame:
    dates = dates or ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    rows = []
    for symbol, values in closes.items():
        for trade_date, close in zip(dates, values, strict=True):
            rows.append(
                {
                    "date": trade_date,
                    "symbol": symbol,
                    "open": close,
                    "close": close,
                }
            )
    return pd.DataFrame(rows)


def test_momentum_topn_signals_at_close_and_executes_next_open():
    prices = _price_frame(
        {
            "000001.SZ": [10, 11, 13, 14, 15],
            "000002.SZ": [10, 10.5, 12, 11, 10],
            "000003.SZ": [10, 10, 10.5, 14, 16],
            "000004.SZ": [10, 9, 9.5, 15, 17],
        }
    )

    result = run_momentum_topn_benchmark(
        prices,
        MomentumTopNConfig(lookback=2, top_n=2, rebalance="D", initial_capital=1_000_000),
    )

    first_signal = result.signals[result.signals["signal_date"] == pd.Timestamp("2024-01-03")]
    assert first_signal["symbol"].tolist() == ["000001.SZ", "000002.SZ"]
    assert first_signal["execute_date"].unique().tolist() == [pd.Timestamp("2024-01-04")]
    assert first_signal["rank"].tolist() == [1, 2]

    first_trade_date = result.trades["trade_date"].min()
    first_trades = result.trades[result.trades["trade_date"] == first_trade_date]
    assert first_trade_date == pd.Timestamp("2024-01-04")
    assert first_trades["side"].tolist() == ["BUY", "BUY"]
    assert first_trades["symbol"].tolist() == ["000001.SZ", "000002.SZ"]


def test_momentum_topn_outputs_equity_holdings_trades_and_signals():
    prices = _price_frame(
        {
            "000001.SZ": [10, 11, 13, 14, 15],
            "000002.SZ": [10, 10.5, 12, 11, 10],
            "000003.SZ": [10, 10, 10.5, 14, 16],
        }
    )

    result = run_momentum_topn_benchmark(
        prices,
        MomentumTopNConfig(lookback=2, top_n=2, rebalance="D", initial_capital=1_000_000),
    )

    assert result.equity_curve.columns.tolist() == [
        "date",
        "total_assets",
        "cash",
        "position_value",
    ]
    assert result.holdings.columns.tolist() == [
        "date",
        "symbol",
        "quantity",
        "close",
        "market_value",
        "weight",
    ]
    assert result.trades.columns.tolist() == [
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
    assert result.signals.columns.tolist() == [
        "signal_date",
        "execute_date",
        "symbol",
        "momentum",
        "rank",
        "target_weight",
    ]
    assert result.equity_curve["date"].tolist() == [
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2024-01-02"),
        pd.Timestamp("2024-01-03"),
        pd.Timestamp("2024-01-04"),
        pd.Timestamp("2024-01-05"),
    ]
    assert not result.holdings.empty
    assert result.equity_curve["cash"].iloc[-1] >= 0


def test_momentum_topn_sells_positions_that_drop_out_of_top_n_before_buying():
    prices = _price_frame(
        {
            "000001.SZ": [10, 10, 13, 10, 10],
            "000002.SZ": [10, 10, 12, 10, 10],
            "000003.SZ": [10, 10, 10, 14, 15],
            "000004.SZ": [10, 10, 9, 13, 14],
        }
    )

    result = run_momentum_topn_benchmark(
        prices,
        MomentumTopNConfig(lookback=2, top_n=2, rebalance="D", initial_capital=1_000_000),
    )

    second_rebalance = result.trades[result.trades["trade_date"] == pd.Timestamp("2024-01-05")]
    assert second_rebalance["side"].tolist() == ["SELL", "SELL", "BUY", "BUY"]
    assert second_rebalance["symbol"].tolist() == [
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
        "000004.SZ",
    ]
    assert (second_rebalance[second_rebalance["side"] == "SELL"]["stamp_tax"] > 0).all()


def test_momentum_topn_validates_required_columns():
    prices = pd.DataFrame({"date": ["2024-01-01"], "symbol": ["000001.SZ"], "close": [10]})

    with pytest.raises(ValueError, match="missing required columns"):
        run_momentum_topn_benchmark(prices)


def test_momentum_topn_validates_config():
    prices = _price_frame(
        {"000001.SZ": [10, 11, 12]},
        dates=["2024-01-01", "2024-01-02", "2024-01-03"],
    )

    with pytest.raises(ValueError, match="lookback"):
        run_momentum_topn_benchmark(prices, MomentumTopNConfig(lookback=0))

    with pytest.raises(ValueError, match="top_n"):
        run_momentum_topn_benchmark(prices, MomentumTopNConfig(top_n=0))
