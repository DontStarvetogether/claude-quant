from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from cq.universe.sources.tushare_pit import (
    PitIndexSpec,
    TusharePitSourceError,
    compress_tushare_weight_snapshots,
    fetch_tushare_pit_universe,
    parse_tushare_index_specs,
)


class FakeTushareClient:
    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[dict[str, str]] = []

    def index_weight(self, **kwargs):
        self.calls.append(kwargs)
        key = (kwargs["index_code"], kwargs["start_date"][:6])
        return self.frames.get(key, pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"]))


def _frame(index_code: str, trade_date: str, rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "index_code": index_code,
                "con_code": symbol,
                "trade_date": trade_date,
                "weight": weight,
            }
            for symbol, weight in rows
        ]
    )


def test_compress_tushare_weight_snapshots_handles_reentry_and_open_intervals():
    weights = pd.DataFrame(
        [
            {"universe_id": "HS300_PIT", "symbol": "000001.SZ", "trade_date": "2024-01-03", "weight": 1.0},
            {"universe_id": "HS300_PIT", "symbol": "600000.SH", "trade_date": "2024-01-03", "weight": 1.0},
            {"universe_id": "HS300_PIT", "symbol": "600000.SH", "trade_date": "2024-02-01", "weight": 1.0},
            {"universe_id": "HS300_PIT", "symbol": "000002.SZ", "trade_date": "2024-02-01", "weight": 1.0},
            {"universe_id": "HS300_PIT", "symbol": "000001.SZ", "trade_date": "2024-03-01", "weight": 1.0},
            {"universe_id": "HS300_PIT", "symbol": "000002.SZ", "trade_date": "2024-03-01", "weight": 1.0},
        ]
    )

    memberships = compress_tushare_weight_snapshots(
        weights,
        [PitIndexSpec("HS300_PIT", "399300.SZ", "沪深300", 1)],
        initial_start=date(2024, 1, 1),
    )

    rows = memberships.assign(
        start_date=memberships["start_date"].dt.strftime("%Y-%m-%d"),
        end_date=memberships["end_date"].dt.strftime("%Y-%m-%d").fillna(""),
    )[["symbol", "start_date", "end_date"]].to_dict("records")
    assert sorted(rows, key=lambda item: (item["symbol"], item["start_date"])) == [
        {"symbol": "000001.SZ", "start_date": "2024-01-01", "end_date": "2024-01-31"},
        {"symbol": "000001.SZ", "start_date": "2024-03-01", "end_date": ""},
        {"symbol": "000002.SZ", "start_date": "2024-02-01", "end_date": ""},
        {"symbol": "600000.SH", "start_date": "2024-01-01", "end_date": "2024-02-29"},
    ]


def test_fetch_tushare_pit_universe_writes_raw_memberships_weights_and_validation(tmp_path):
    spec = PitIndexSpec("HS300_PIT", "399300.SZ", "沪深300", 1)
    client = FakeTushareClient(
        {
            ("399300.SZ", "202401"): _frame(
                "399300.SZ",
                "20240103",
                [("000001.SZ", 0.9), ("600000.SH", 0.8)],
            ),
            ("399300.SZ", "202402"): _frame(
                "399300.SZ",
                "20240201",
                [("600000.SH", 0.7), ("000002.SZ", 0.6), ("000002.SZ", 0.6)],
            ),
            ("399300.SZ", "202403"): _frame(
                "399300.SZ",
                "20240301",
                [("000001.SZ", 0.5), ("000002.SZ", 0.4)],
            ),
        }
    )

    result = fetch_tushare_pit_universe(
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
        output_csv=tmp_path / "pit_memberships.csv",
        weights_output=tmp_path / "pit_weights.csv",
        raw_dir=tmp_path / "raw",
        validation_dir=tmp_path / "validation",
        index_specs=[spec],
        client=client,
        min_symbols=1,
    )

    assert result.validation_result.passed is True
    assert result.output_csv.exists()
    assert result.weights_output.exists()
    assert len(result.raw_files) == 3
    assert [call["start_date"] for call in client.calls] == ["20240101", "20240201", "20240301"]
    weights = pd.read_csv(result.weights_output)
    assert weights.columns.tolist() == ["universe_id", "symbol", "trade_date", "weight"]
    assert weights["symbol"].tolist() == [
        "000001.SZ",
        "600000.SH",
        "000002.SZ",
        "600000.SH",
        "000001.SZ",
        "000002.SZ",
    ]
    summary = json.loads((tmp_path / "validation" / "pit_validation_summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] is True
    fetch_summary = json.loads((tmp_path / "validation" / "pit_fetch_summary.json").read_text(encoding="utf-8"))
    assert fetch_summary["provider"] == "tushare"
    assert fetch_summary["source_quality"] == "strict_historical_index_weight"
    assert fetch_summary["strict_historical_pit"] is True
    assert fetch_summary["snapshot_dates"]["hs300_pit"] == "2024-01-03 至 2024-03-01"
    fetch_report = (tmp_path / "validation" / "pit_fetch_report.md").read_text(encoding="utf-8")
    assert "| 数据源 | tushare |" in fetch_report
    assert "| 严格历史 PIT | 是 |" in fetch_report
    assert "| hs300_pit | 2024-01-03 至 2024-03-01 |" in fetch_report
    sidecar_summary = json.loads((tmp_path / "pit_memberships.summary.json").read_text(encoding="utf-8"))
    assert sidecar_summary["provider"] == "tushare"
    assert sidecar_summary["report_path"].endswith("pit_fetch_report.md")


def test_fetch_tushare_pit_universe_requires_token_without_injected_client(tmp_path):
    with pytest.raises(TusharePitSourceError, match="TUSHARE_TOKEN"):
        fetch_tushare_pit_universe(
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            output_csv=tmp_path / "pit.csv",
            weights_output=tmp_path / "weights.csv",
            raw_dir=tmp_path / "raw",
            validation_dir=tmp_path / "validation",
            token=None,
        )


def test_parse_tushare_index_specs_uses_defaults_and_rejects_bad_input():
    default = parse_tushare_index_specs([])
    assert [spec.universe_id for spec in default] == ["HS300_PIT", "ZZ500_PIT", "ZZ1000_PIT"]

    custom = parse_tushare_index_specs(["HS300_PIT=399300.SZ", "custom=000001.SH"])
    assert custom[0].name == "沪深300"
    assert custom[1].name == "CUSTOM"
    assert custom[1].min_symbols == 1

    with pytest.raises(ValueError, match="UNIVERSE_ID=INDEX_CODE"):
        parse_tushare_index_specs(["bad"])

    with pytest.raises(ValueError, match="duplicate universe"):
        parse_tushare_index_specs(["A=000001.SH", "a=000002.SH"])
