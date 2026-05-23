from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from cq.data.store.parquet_store import ParquetStore
from cq.universe import (
    LiquidUniverseConfig,
    LiquidUniverseProvider,
    StoreBackedLiquidUniverseProvider,
    UniverseNotFoundError,
    build_all_a_liquid_universe,
    select_all_a_liquid_universe,
)


def _make_rows(
    symbol: str,
    dates: list[date],
    *,
    amount: float = 100_000_000.0,
    volume: float = 1_000_000.0,
    close: float = 10.0,
    is_st: bool = False,
    is_suspended: bool = False,
    zero_volume_on: date | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for trade_date in dates:
        rows.append(
            {
                "trade_date": trade_date,
                "symbol": symbol,
                "close": close,
                "amount": amount,
                "volume": 0.0 if trade_date == zero_volume_on else volume,
                "is_st": is_st and trade_date == dates[-1],
                "is_suspended": is_suspended and trade_date == dates[-1],
            }
        )
    return rows


def test_all_a_liquid_screen_filters_untradeable_symbols_and_reports_reasons():
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(5)]
    target = dates[-1]
    rows: list[dict[str, object]] = []
    rows += _make_rows("GOOD.SH", dates)
    rows += _make_rows("LOW.SH", dates, amount=10_000_000.0)
    rows += _make_rows("ST.SH", dates, is_st=True)
    rows += _make_rows("SUSP.SH", dates, is_suspended=True)
    rows += _make_rows("NEW.SH", dates[-2:])
    rows += _make_rows("STALE.SH", dates[:-1])
    rows += _make_rows("ZERO.SH", dates, zero_volume_on=dates[-2])
    rows += _make_rows("PRICE.SH", dates, close=0.5)

    config = LiquidUniverseConfig(
        lookback_days=3,
        min_listing_days=3,
        min_avg_amount=50_000_000.0,
        min_price=1.0,
        max_zero_volume_days=0,
    )

    selection = select_all_a_liquid_universe(pd.DataFrame(rows), target, config)

    assert selection.symbols == ("GOOD.SH",)
    reasons = selection.diagnostics.set_index("symbol")["reason"].to_dict()
    assert reasons == {
        "GOOD.SH": "selected",
        "LOW.SH": "low_avg_amount",
        "NEW.SH": "insufficient_listing_days",
        "PRICE.SH": "abnormal_price",
        "STALE.SH": "missing_latest_bar",
        "ST.SH": "st",
        "SUSP.SH": "suspended",
        "ZERO.SH": "zero_volume",
    }


def test_all_a_liquid_screen_orders_by_liquidity_and_applies_top_n():
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(4)]
    rows: list[dict[str, object]] = []
    rows += _make_rows("MID.SH", dates, amount=80_000_000.0)
    rows += _make_rows("HIGH.SH", dates, amount=200_000_000.0)
    rows += _make_rows("LOW.SH", dates, amount=60_000_000.0)

    config = LiquidUniverseConfig(
        lookback_days=3,
        min_listing_days=3,
        min_avg_amount=50_000_000.0,
        top_n=2,
    )

    symbols = build_all_a_liquid_universe(pd.DataFrame(rows), dates[-1], config)

    assert symbols == ["HIGH.SH", "MID.SH"]


def test_liquid_universe_provider_requires_trade_date_and_resolves_aliases():
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(3)]
    bars = pd.DataFrame(_make_rows("GOOD.SH", dates))
    provider = LiquidUniverseProvider(
        bars,
        LiquidUniverseConfig(
            lookback_days=2,
            min_listing_days=2,
            min_avg_amount=50_000_000.0,
        ),
    )

    universe = provider.get_universe("ALL_A_LIQUID")
    assert universe.id == "all_a_liquid"
    assert universe.construction == "dynamic_liquidity"
    assert universe.symbols == ()
    assert universe.metadata["rules"]["lookback_days"] == 2
    assert provider.get_symbols("preset_all_a_liquid", dates[-1]) == ["GOOD.SH"]

    with pytest.raises(ValueError, match="trade_date is required"):
        provider.get_symbols("all_a_liquid")

    with pytest.raises(UniverseNotFoundError):
        provider.get_symbols("missing", dates[-1])


def test_liquid_universe_rejects_missing_required_columns():
    bars = pd.DataFrame({"trade_date": [date(2024, 1, 2)], "symbol": ["GOOD.SH"]})

    with pytest.raises(ValueError, match="close"):
        build_all_a_liquid_universe(bars, date(2024, 1, 2))


def test_store_backed_liquid_universe_provider_reads_local_parquet_store(tmp_path):
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(5)]
    store = ParquetStore(tmp_path)
    for symbol, amount in {"GOOD.SH": 100_000_000.0, "LOW.SH": 10_000_000.0}.items():
        bars = pd.DataFrame(_make_rows(symbol, dates, amount=amount)).drop(columns=["symbol"])
        store.write_daily_bars(symbol, bars, adjust="qfq", mode="overwrite")

    assert store.list_symbols("qfq") == ["GOOD.SH", "LOW.SH"]

    provider = StoreBackedLiquidUniverseProvider(
        store,
        LiquidUniverseConfig(
            lookback_days=3,
            min_listing_days=3,
            min_avg_amount=50_000_000.0,
        ),
    )

    selection = provider.select(dates[-1])

    assert provider.get_symbols("all_a_liquid", dates[-1]) == ["GOOD.SH"]
    assert selection.diagnostics.set_index("symbol").loc["LOW.SH", "reason"] == "low_avg_amount"


def test_store_backed_liquid_universe_provider_accepts_explicit_candidates(tmp_path):
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(4)]
    store = ParquetStore(tmp_path)
    for symbol in ["KEEP.SH", "DROP.SH"]:
        bars = pd.DataFrame(_make_rows(symbol, dates)).drop(columns=["symbol"])
        store.write_daily_bars(symbol, bars, adjust="qfq", mode="overwrite")

    provider = StoreBackedLiquidUniverseProvider(
        store,
        LiquidUniverseConfig(
            lookback_days=2,
            min_listing_days=2,
            min_avg_amount=50_000_000.0,
        ),
        candidate_symbols=["KEEP.SH"],
    )

    assert provider.get_symbols("preset_all_a_liquid", dates[-1]) == ["KEEP.SH"]
