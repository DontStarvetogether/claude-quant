"""单元测试：DataPipeline 数据更新诊断。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from cq.data.calendar import TradingCalendar
from cq.data.pipeline import DataPipeline
from cq.data.source.base import DataSource
from cq.data.store.parquet_store import ParquetStore
from web.routers.data import _summarize_download_results


class FakeSource(DataSource):
    def __init__(
        self,
        bars: pd.DataFrame | None = None,
        adj: pd.DataFrame | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.bars = bars if bars is not None else pd.DataFrame()
        self.adj = adj if adj is not None else pd.DataFrame()
        self.exc = exc

    def fetch_daily_bars(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        if self.exc:
            raise self.exc
        if self.bars.empty:
            return pd.DataFrame()
        df = self.bars.copy()
        dates = pd.to_datetime(df["trade_date"]).dt.date
        return df[(dates >= start_date) & (dates <= end_date)].reset_index(drop=True)

    def fetch_adj_factors(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        if self.adj.empty:
            return pd.DataFrame()
        df = self.adj.copy()
        dates = pd.to_datetime(df["trade_date"]).dt.date
        return df[(dates >= start_date) & (dates <= end_date)].reset_index(drop=True)

    def fetch_trading_calendar(self, exchange: str, year: int) -> list[date]:
        return []

    def fetch_stock_info(self, symbol: str) -> dict:
        return {"list_date": date(2024, 1, 2)}


def make_bars(dates: list[date]) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": dates,
        "open": [10.0 + i for i in range(len(dates))],
        "high": [10.5 + i for i in range(len(dates))],
        "low": [9.5 + i for i in range(len(dates))],
        "close": [10.2 + i for i in range(len(dates))],
        "volume": [1000] * len(dates),
        "amount": [10000.0] * len(dates),
        "pre_close": [10.0 + i for i in range(len(dates))],
        "is_st": [False] * len(dates),
        "is_suspended": [False] * len(dates),
    })


def make_adj(dates: list[date]) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": dates,
        "adj_factor": [1.0] * len(dates),
    })


def make_pipeline(tmp_path, source: DataSource) -> DataPipeline:
    store = ParquetStore(tmp_path)
    calendar = TradingCalendar([
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ])
    return DataPipeline(source, store, calendar)


def test_update_symbol_diagnostic_updated(tmp_path):
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    pipeline = make_pipeline(tmp_path, FakeSource(make_bars(dates), make_adj(dates)))

    diag = pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))

    assert diag.status == "updated"
    assert diag.new_records == 2
    assert diag.used_cache is True
    assert diag.local_first_date == "2024-01-02"
    assert diag.local_last_date == "2024-01-03"
    assert diag.source == "FakeSource"
    assert diag.cache_path is not None
    assert diag.raw_first_date == "2024-01-02"
    assert diag.qfq_first_date == "2024-01-02"
    assert diag.qfq_available is True
    assert diag.factor_available is True
    assert diag.st_status_source == "unavailable"
    assert diag.limit_price_source == "exchange_or_calculated"
    assert diag.quality_level == "pass"
    assert diag.data_quality["quality_level"] == "pass"


def test_update_symbol_diagnostic_cache_hit(tmp_path):
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    pipeline = make_pipeline(tmp_path, FakeSource(make_bars(dates), make_adj(dates)))
    pipeline.update_symbol("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))

    diag = pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))

    assert diag.status == "cache_hit"
    assert diag.new_records == 0
    assert diag.used_cache is True


def test_update_symbol_diagnostic_empty_source_without_cache(tmp_path):
    pipeline = make_pipeline(tmp_path, FakeSource(pd.DataFrame()))

    diag = pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))

    assert diag.status == "empty_source"
    assert diag.used_cache is False


def test_update_symbol_diagnostic_failed_without_cache(tmp_path):
    pipeline = make_pipeline(tmp_path, FakeSource(exc=RuntimeError("network down")))

    diag = pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))

    assert diag.status == "download_failed_no_cache"
    assert diag.used_cache is False
    assert diag.error == "network down"


def test_update_symbol_diagnostic_failed_with_cache(tmp_path):
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    pipeline = make_pipeline(tmp_path, FakeSource(make_bars(dates), make_adj(dates)))
    pipeline.update_symbol("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))

    failing_pipeline = make_pipeline(tmp_path, FakeSource(exc=RuntimeError("network down")))
    diag = failing_pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 4), start_date=date(2024, 1, 2))

    assert diag.status == "download_failed_cache_available"
    assert diag.used_cache is True
    assert diag.local_last_date == "2024-01-03"
    assert diag.error == "network down"


def test_download_progress_summary_counts_statuses():
    summary = _summarize_download_results(
        [
            {"status": "updated"},
            {"status": "cache_hit"},
            {"status": "download_failed_cache_available"},
            {"status": "download_failed_no_cache"},
            {"status": "empty_source"},
        ],
        total=5,
    )

    assert summary == {
        "total": 5,
        "updated": 1,
        "cache_hit": 1,
        "failed": 2,
        "missing": 2,
    }


def test_tail_update_recalculates_qfq_when_adj_factor_changes(tmp_path):
    store = ParquetStore(tmp_path)
    calendar = TradingCalendar([date(2024, 1, 2), date(2024, 1, 3)])
    bars = make_bars([date(2024, 1, 2), date(2024, 1, 3)])
    bars.loc[bars["trade_date"] == date(2024, 1, 2), ["open", "high", "low", "close"]] = 10.0
    bars.loc[bars["trade_date"] == date(2024, 1, 3), ["open", "high", "low", "close"]] = 20.0
    adj = pd.DataFrame({
        "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
        "adj_factor": [1.0, 2.0],
    })

    pipeline = DataPipeline(FakeSource(bars, adj), store, calendar)
    pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 2), start_date=date(2024, 1, 2))
    qfq_before = store.read_daily_bars("600519.SH", adjust="qfq")
    assert float(qfq_before.loc[0, "close"]) == 10.0

    pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))
    qfq_after = store.read_daily_bars("600519.SH", adjust="qfq")

    assert qfq_after["close"].tolist() == [20.0, 20.0]
    assert qfq_after["adj_factor"].tolist() == [2.0, 1.0]
    assert qfq_after.loc[0, "pre_close"] == 20.0
    assert qfq_after.loc[0, "limit_up"] == 22.0
    assert qfq_after.loc[0, "limit_down"] == 18.0
    diag = pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))
    assert diag.data_quality["status"] == "ok"


def test_cache_hit_repairs_stale_qfq_limit_price_scale(tmp_path):
    store = ParquetStore(tmp_path)
    calendar = TradingCalendar([date(2024, 1, 2), date(2024, 1, 3)])
    bars = make_bars([date(2024, 1, 2), date(2024, 1, 3)])
    bars.loc[bars["trade_date"] == date(2024, 1, 2), ["open", "high", "low", "close"]] = 10.0
    bars.loc[bars["trade_date"] == date(2024, 1, 3), ["open", "high", "low", "close"]] = 20.0
    adj = pd.DataFrame({
        "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
        "adj_factor": [1.0, 2.0],
    })

    pipeline = DataPipeline(FakeSource(bars, adj), store, calendar)
    pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))

    stale_qfq = store.read_daily_bars("600519.SH", adjust="qfq")
    stale_qfq.loc[0, "pre_close"] = 10.0
    stale_qfq.loc[0, "limit_up"] = 11.0
    stale_qfq.loc[0, "limit_down"] = 9.0
    store.write_daily_bars("600519.SH", stale_qfq, adjust="qfq", mode="overwrite")

    diag = pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 2))
    repaired = store.read_daily_bars("600519.SH", adjust="qfq")

    assert diag.status == "cache_hit"
    assert diag.data_quality["status"] == "ok"
    assert diag.quality_level == "pass"
    assert "qfq_price_scale_mismatch" not in diag.data_quality["warnings"]
    assert repaired.loc[0, "pre_close"] == 20.0
    assert repaired.loc[0, "limit_up"] == 22.0
    assert repaired.loc[0, "limit_down"] == 18.0


def test_data_quality_allows_pre_listing_gap(tmp_path):
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    pipeline = make_pipeline(tmp_path, FakeSource(make_bars(dates), make_adj(dates)))

    diag = pipeline.update_symbol_diagnostic("600519.SH", date(2024, 1, 3), start_date=date(2024, 1, 1))

    assert diag.list_date == "2024-01-02"
    assert diag.coverage_status == "pre_listing_gap"
    assert diag.data_quality["status"] == "ok"
    assert "pre_listing_gap" in diag.data_quality["warnings"]
