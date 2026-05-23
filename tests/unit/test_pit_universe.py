from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from cq.universe import PointInTimeUniverseProvider, UniverseNotFoundError


def _memberships() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "universe_id": "HS300_PIT",
                "name": "沪深300历史成分股",
                "symbol": "600519.SH",
                "start_date": "2020-01-01",
                "end_date": "",
            },
            {
                "universe_id": "HS300_PIT",
                "name": "沪深300历史成分股",
                "symbol": "000001.SZ",
                "start_date": "2020-01-01",
                "end_date": "2021-12-31",
            },
            {
                "universe_id": "HS300_PIT",
                "name": "沪深300历史成分股",
                "symbol": "300750.SZ",
                "start_date": "2022-01-01",
                "end_date": "",
            },
            {
                "universe_id": "ZZ500_PIT",
                "name": "中证500历史成分股",
                "symbol": "600000.SH",
                "start_date": "2020-01-01",
                "end_date": "",
            },
        ]
    )


def test_point_in_time_universe_resolves_members_by_trade_date():
    provider = PointInTimeUniverseProvider(_memberships())

    early = provider.get_symbols("HS300_PIT", date(2021, 6, 1))
    late = provider.get_symbols("preset_hs300_pit", date(2022, 6, 1))

    assert early == ["000001.SZ", "600519.SH"]
    assert late == ["300750.SZ", "600519.SH"]


def test_point_in_time_universe_lists_metadata_and_requires_trade_date():
    provider = PointInTimeUniverseProvider(_memberships())

    universes = provider.list_universes()
    hs300 = provider.get_universe("hs300_pit")

    assert {universe.id for universe in universes} == {"hs300_pit", "zz500_pit"}
    assert hs300.name == "沪深300历史成分股"
    assert hs300.construction == "point_in_time"
    assert hs300.metadata["member_count"] == 3

    with pytest.raises(ValueError, match="trade_date is required"):
        provider.get_symbols("hs300_pit")

    with pytest.raises(UniverseNotFoundError):
        provider.get_symbols("missing", date(2022, 1, 1))


def test_point_in_time_universe_can_load_from_csv(tmp_path):
    path = tmp_path / "membership.csv"
    _memberships().to_csv(path, index=False)

    provider = PointInTimeUniverseProvider.from_csv(path)

    assert provider.get_symbols("zz500_pit", date(2022, 1, 1)) == ["600000.SH"]


def test_point_in_time_universe_validates_required_columns():
    with pytest.raises(ValueError, match="memberships missing required columns"):
        PointInTimeUniverseProvider(pd.DataFrame({"symbol": ["600519.SH"]}))
