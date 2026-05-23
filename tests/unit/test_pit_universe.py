from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from cq.universe import (
    PointInTimeUniverseProvider,
    UniverseNotFoundError,
    export_pit_validation_result,
    filter_prices_by_pit_universe,
    import_pit_memberships,
    pit_membership_diagnostics,
    validate_pit_memberships,
)


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


def test_import_pit_memberships_normalizes_external_file(tmp_path):
    source = tmp_path / "external.csv"
    source.write_text(
        "universe_id,symbol,start_date,end_date,name\n"
        "HS300_PIT,600519.sh,2020-01-01,,沪深300\n"
        "HS300_PIT,000001.sz,2020-01-01,2021-12-31,沪深300\n",
        encoding="utf-8",
    )
    output = tmp_path / "pit_membership.csv"
    diagnostics = tmp_path / "diagnostics.json"

    result = import_pit_memberships(source, output, diagnostics_path=diagnostics)

    assert result.output_csv == output
    assert diagnostics.exists()
    normalized = pd.read_csv(output)
    assert normalized["universe_id"].tolist() == ["hs300_pit", "hs300_pit"]
    assert normalized["symbol"].tolist() == ["000001.SZ", "600519.SH"]
    assert result.summary["universes"]["hs300_pit"]["symbols"] == 2


def test_pit_membership_diagnostics_reports_open_ended_rows():
    summary = pit_membership_diagnostics(_memberships())

    assert summary["universe_count"] == 2
    assert summary["universes"]["hs300_pit"]["open_ended_rows"] == 2


def test_filter_prices_by_pit_universe_uses_each_trade_date_membership():
    provider = PointInTimeUniverseProvider(_memberships())
    prices = pd.DataFrame(
        [
            {"date": "2021-06-01", "symbol": "000001.SZ", "close": 10},
            {"date": "2021-06-01", "symbol": "300750.SZ", "close": 20},
            {"date": "2022-06-01", "symbol": "000001.SZ", "close": 11},
            {"date": "2022-06-01", "symbol": "300750.SZ", "close": 21},
        ]
    )

    filtered, diagnostics = filter_prices_by_pit_universe(prices, provider, "HS300_PIT")

    assert filtered[["date", "symbol"]].to_dict("records") == [
        {"date": "2021-06-01", "symbol": "000001.SZ"},
        {"date": "2022-06-01", "symbol": "300750.SZ"},
    ]
    assert diagnostics["dates"] == 2
    assert diagnostics["min_members"] == 2


def test_validate_pit_memberships_reports_expected_universe_and_overlap(tmp_path):
    memberships = pd.DataFrame(
        [
            {
                "universe_id": "HS300_PIT",
                "symbol": "000001.SZ",
                "start_date": "2020-01-01",
                "end_date": "2020-12-31",
                "name": "沪深300",
            },
            {
                "universe_id": "HS300_PIT",
                "symbol": "000001.SZ",
                "start_date": "2020-06-01",
                "end_date": "",
                "name": "沪深300",
            },
            {
                "universe_id": "HS300_PIT",
                "symbol": "bad",
                "start_date": "2020-01-01",
                "end_date": "",
                "name": "沪深300",
            },
        ]
    )

    result = validate_pit_memberships(
        memberships,
        expected_universes=["HS300_PIT", "ZZ500_PIT"],
        min_symbols=3,
        coverage_start="2019-01-01",
        coverage_end="2020-07-01",
    )
    exported = export_pit_validation_result(result, tmp_path)

    codes = {issue.code for issue in result.issues}
    assert result.passed is False
    assert "missing_expected_universe" in codes
    assert "overlapping_interval" in codes
    assert "invalid_symbol_format" in codes
    assert "empty_coverage_date" in codes
    assert exported.files["summary"].exists()
    assert exported.files["issues"].exists()
    assert exported.files["report"].read_text(encoding="utf-8").startswith("# PIT 股票池校验报告")


def test_validate_pit_memberships_passes_clean_file():
    memberships = pd.DataFrame(
        [
            {
                "universe_id": "HS300_PIT",
                "symbol": "000001.SZ",
                "start_date": "2020-01-01",
                "end_date": "2020-12-31",
                "name": "沪深300",
            },
            {
                "universe_id": "HS300_PIT",
                "symbol": "600000.SH",
                "start_date": "2020-01-01",
                "end_date": "",
                "name": "沪深300",
            },
        ]
    )

    result = validate_pit_memberships(
        memberships,
        expected_universes=["HS300_PIT"],
        min_symbols=2,
        coverage_start="2020-06-01",
        coverage_end="2020-12-31",
    )

    assert result.passed is True
    assert result.summary["schema_version"] == "pit_validation.v1"
    assert result.issues == []
