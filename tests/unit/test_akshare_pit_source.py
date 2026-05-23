from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from cq.universe.sources.akshare_pit import (
    AksharePitSourceError,
    compress_akshare_latest_snapshots,
    fetch_akshare_pit_universe,
    normalize_akshare_snapshot,
    parse_akshare_index_specs,
)
from cq.universe.sources.tushare_pit import PitIndexSpec


class FakeAkshareClient:
    def __init__(
        self,
        *,
        weight_frames: dict[str, pd.DataFrame] | None = None,
        cons_frames: dict[str, pd.DataFrame] | None = None,
        fail_weight: bool = False,
    ) -> None:
        self.weight_frames = weight_frames or {}
        self.cons_frames = cons_frames or {}
        self.fail_weight = fail_weight
        self.weight_calls: list[str] = []
        self.cons_calls: list[str] = []

    def index_stock_cons_weight_csindex(self, symbol: str) -> pd.DataFrame:
        self.weight_calls.append(symbol)
        if self.fail_weight:
            raise RuntimeError("network error")
        return self.weight_frames.get(symbol, pd.DataFrame())

    def index_stock_cons_csindex(self, symbol: str) -> pd.DataFrame:
        self.cons_calls.append(symbol)
        return self.cons_frames.get(symbol, pd.DataFrame())


def _ak_weight_frame(index_code: str, trade_date: str, rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": trade_date,
                "指数代码": index_code,
                "指数名称": "沪深300",
                "成分券代码": symbol,
                "成分券名称": symbol,
                "交易所": exchange,
                "权重": weight,
            }
            for symbol, exchange, weight in rows
        ]
    )


def _ak_cons_frame(index_code: str, trade_date: str, rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": trade_date,
                "指数代码": index_code,
                "指数名称": "沪深300",
                "成分券代码": symbol,
                "成分券名称": symbol,
                "交易所": exchange,
            }
            for symbol, exchange in rows
        ]
    )


def test_normalize_akshare_snapshot_adds_exchange_suffix_and_keeps_weights():
    spec = PitIndexSpec("HS300_PIT", "000300", "沪深300", 1)
    normalized = normalize_akshare_snapshot(
        _ak_weight_frame(
            "000300",
            "2024-05-31",
            [
                ("000001", "深圳证券交易所", 1.23),
                ("600000", "上海证券交易所", 0.88),
                ("430047", "北京证券交易所", 0.12),
            ],
        ),
        spec,
    )

    assert normalized["symbol"].tolist() == ["000001.SZ", "600000.SH", "430047.BJ"]
    assert normalized["weight"].tolist() == [1.23, 0.88, 0.12]


def test_compress_akshare_latest_snapshots_uses_latest_snapshot_only():
    spec = PitIndexSpec("HS300_PIT", "000300", "沪深300", 1)
    weights = pd.DataFrame(
        [
            {"universe_id": "HS300_PIT", "symbol": "000001.SZ", "trade_date": "2024-04-30", "weight": 1.0},
            {"universe_id": "HS300_PIT", "symbol": "600000.SH", "trade_date": "2024-05-31", "weight": 1.0},
        ]
    )

    memberships = compress_akshare_latest_snapshots(weights, [spec])

    rows = memberships.assign(
        start_date=memberships["start_date"].dt.strftime("%Y-%m-%d"),
        end_date=memberships["end_date"].dt.strftime("%Y-%m-%d").fillna(""),
    )[["symbol", "start_date", "end_date"]].to_dict("records")
    assert rows == [{"symbol": "600000.SH", "start_date": "2024-05-31", "end_date": ""}]


def test_fetch_akshare_pit_universe_writes_latest_snapshot_outputs(tmp_path):
    spec = PitIndexSpec("HS300_PIT", "000300", "沪深300", 1)
    client = FakeAkshareClient(
        weight_frames={
            "000300": _ak_weight_frame(
                "000300",
                "2024-05-31",
                [("000001", "深圳证券交易所", 1.2), ("600000", "上海证券交易所", 0.8)],
            )
        }
    )

    result = fetch_akshare_pit_universe(
        start=date(2020, 1, 1),
        end=date(2024, 12, 31),
        output_csv=tmp_path / "pit_memberships.csv",
        weights_output=tmp_path / "pit_weights.csv",
        raw_dir=tmp_path / "raw",
        validation_dir=tmp_path / "validation",
        index_specs=[spec],
        client=client,
        min_symbols=1,
    )

    assert result.validation_result.passed is True
    assert result.summary["strict_historical_pit"] is False
    assert result.summary["requested_start"] == "2020-01-01"
    assert result.summary["effective_coverage_start"] == "2024-05-31"
    assert result.raw_files[0].exists()
    fetch_summary = json.loads((tmp_path / "validation" / "pit_fetch_summary.json").read_text(encoding="utf-8"))
    assert fetch_summary["provider"] == "akshare"
    assert fetch_summary["source_quality"] == "free_best_effort_latest_snapshot"
    assert fetch_summary["strict_historical_pit"] is False
    assert fetch_summary["effective_coverage_start"] == "2024-05-31"
    assert fetch_summary["report_path"].endswith("pit_fetch_report.md")
    fetch_report = (tmp_path / "validation" / "pit_fetch_report.md").read_text(encoding="utf-8")
    assert "| 数据源 | akshare |" in fetch_report
    assert "| 严格历史 PIT | 否 |" in fetch_report
    assert "AkShare 免费源只提供公开最新快照" in fetch_report
    sidecar_summary = json.loads((tmp_path / "pit_memberships.summary.json").read_text(encoding="utf-8"))
    assert sidecar_summary["summary_path"].endswith("pit_fetch_summary.json")
    assert sidecar_summary["report_path"].endswith("pit_fetch_report.md")
    assert sidecar_summary["sidecar_summary_path"].endswith("pit_memberships.summary.json")
    assert client.weight_calls == ["000300"]
    assert client.cons_calls == []
    memberships = pd.read_csv(result.output_csv)
    assert memberships["start_date"].unique().tolist() == ["2024-05-31"]
    weights = pd.read_csv(result.weights_output)
    assert weights["weight"].tolist() == [1.2, 0.8]


def test_fetch_akshare_pit_universe_uses_common_snapshot_start_for_validation(tmp_path):
    specs = [
        PitIndexSpec("HS300_PIT", "000300", "沪深300", 1),
        PitIndexSpec("ZZ500_PIT", "000905", "中证500", 1),
    ]
    client = FakeAkshareClient(
        weight_frames={
            "000300": _ak_weight_frame("000300", "2024-05-31", [("000001", "深圳证券交易所", 1.0)]),
            "000905": _ak_weight_frame("000905", "2024-06-28", [("600000", "上海证券交易所", 1.0)]),
        }
    )

    result = fetch_akshare_pit_universe(
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        output_csv=tmp_path / "pit_memberships.csv",
        weights_output=tmp_path / "pit_weights.csv",
        raw_dir=tmp_path / "raw",
        validation_dir=tmp_path / "validation",
        index_specs=specs,
        client=client,
        min_symbols=1,
    )

    assert result.validation_result.passed is True
    assert result.summary["effective_coverage_start"] == "2024-06-28"


def test_fetch_akshare_pit_universe_falls_back_to_constituents_without_weight(tmp_path):
    spec = PitIndexSpec("HS300_PIT", "000300", "沪深300", 1)
    client = FakeAkshareClient(
        cons_frames={
            "000300": _ak_cons_frame(
                "000300",
                "2024-06-03",
                [("000001", "深圳证券交易所"), ("600000", "上海证券交易所")],
            )
        },
        fail_weight=True,
    )

    result = fetch_akshare_pit_universe(
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
        output_csv=tmp_path / "pit_memberships.csv",
        weights_output=tmp_path / "pit_weights.csv",
        raw_dir=tmp_path / "raw",
        validation_dir=tmp_path / "validation",
        index_specs=[spec],
        client=client,
        min_symbols=1,
    )

    assert result.summary["endpoints"]["hs300_pit"] == "index_stock_cons_csindex"
    weights = pd.read_csv(result.weights_output)
    assert weights["weight"].isna().all()


def test_fetch_akshare_pit_universe_rejects_empty_sources(tmp_path):
    spec = PitIndexSpec("HS300_PIT", "000300", "沪深300", 1)
    client = FakeAkshareClient()

    with pytest.raises(AksharePitSourceError, match="返回空数据"):
        fetch_akshare_pit_universe(
            start=date(2024, 1, 1),
            end=date(2024, 12, 31),
            output_csv=tmp_path / "pit.csv",
            weights_output=tmp_path / "weights.csv",
            raw_dir=tmp_path / "raw",
            validation_dir=tmp_path / "validation",
            index_specs=[spec],
            client=client,
        )


def test_parse_akshare_index_specs_uses_defaults_and_rejects_bad_input():
    default = parse_akshare_index_specs([])
    assert [spec.index_code for spec in default] == ["000300", "000905", "000852"]

    custom = parse_akshare_index_specs(["HS300_PIT=000300", "custom=932000"])
    assert custom[0].name == "沪深300"
    assert custom[1].name == "CUSTOM"
    assert custom[1].min_symbols == 1

    with pytest.raises(ValueError, match="UNIVERSE_ID=INDEX_CODE"):
        parse_akshare_index_specs(["bad"])

    with pytest.raises(ValueError, match="duplicate universe"):
        parse_akshare_index_specs(["A=000001", "a=000002"])
