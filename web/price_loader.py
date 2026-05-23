"""Price data loading helpers shared by Web research and benchmark jobs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from cq.data.store.parquet_store import ParquetStore
from cq.universe import (
    PointInTimeUniverseProvider,
    UniverseNotFoundError,
    get_builtin_universe_provider,
)
from cq.utils.config import Config


@dataclass(frozen=True)
class PriceLoadResult:
    prices: pd.DataFrame
    diagnostics: dict[str, Any]


def load_price_data(
    request: dict[str, Any],
    *,
    required_columns: list[str],
    buffer_days_before: int = 0,
    buffer_days_after: int = 0,
) -> PriceLoadResult:
    """Load long-form price data from CSV or the local downloaded cache."""

    price_csv = request.get("price_csv")
    price_source = str(request.get("price_source") or "local_cache")
    if price_csv:
        return _load_csv_prices(Path(str(price_csv)).expanduser(), required_columns)
    if price_source == "csv":
        raise ValueError("price_csv is required when price_source=csv")
    if price_source != "local_cache":
        raise ValueError(f"未知价格数据源: {price_source}")
    return _load_local_cache_prices(
        request,
        required_columns=required_columns,
        buffer_days_before=buffer_days_before,
        buffer_days_after=buffer_days_after,
    )


def _load_csv_prices(path: Path, required_columns: list[str]) -> PriceLoadResult:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"价格数据 CSV 不存在: {path}")
    prices = pd.read_csv(path)
    required = ["date", "symbol", *required_columns]
    _require_columns(prices, required)
    data = prices[required].copy()
    data["date"] = pd.to_datetime(data["date"])
    data["symbol"] = data["symbol"].astype(str).str.upper()
    for column in required_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=required)
    data = data.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)
    return PriceLoadResult(
        prices=data,
        diagnostics={
            "source": "csv",
            "path": str(path),
            "quality": "external_csv",
            "rows": int(len(data)),
            "symbols_loaded": int(data["symbol"].nunique()),
        },
    )


def _load_local_cache_prices(
    request: dict[str, Any],
    *,
    required_columns: list[str],
    buffer_days_before: int,
    buffer_days_after: int,
) -> PriceLoadResult:
    adjust = str(request.get("adjust") or "qfq")
    data_root = _data_root(request)
    store = ParquetStore(data_root)
    symbols, universe_diag = _resolve_symbols(request, store, adjust)
    start_date = _optional_date(request.get("start_date"))
    end_date = _optional_date(request.get("end_date"))
    read_start = start_date - timedelta(days=buffer_days_before) if start_date else None
    read_end = end_date + timedelta(days=buffer_days_after) if end_date else None

    raw = store.read_bars_batch(symbols, start_date=read_start, end_date=read_end, adjust=adjust)
    if raw.empty:
        raise ValueError(
            "本地行情缓存没有可用数据；请先在数据管理页下载行情，"
            "或把 price_source 改为 csv 并提供 price_csv"
        )

    required = ["symbol", "trade_date", *required_columns]
    _require_columns(raw, required)
    data = raw[required].copy().rename(columns={"trade_date": "date"})
    data["date"] = pd.to_datetime(data["date"])
    data["symbol"] = data["symbol"].astype(str).str.upper()
    for column in required_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["date", "symbol", *required_columns])
    data = data.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)
    if data.empty:
        raise ValueError("本地行情缓存读取成功，但缺少有效价格字段")

    loaded_symbols = set(data["symbol"].unique())
    diagnostics = {
        "source": "local_cache",
        "quality": universe_diag.get("quality", "local_downloaded_cache"),
        "data_root": str(data_root),
        "adjust": adjust,
        "requested_start_date": start_date.isoformat() if start_date else None,
        "requested_end_date": end_date.isoformat() if end_date else None,
        "read_start_date": read_start.isoformat() if read_start else None,
        "read_end_date": read_end.isoformat() if read_end else None,
        "requested_symbols": len(symbols),
        "symbols_loaded": len(loaded_symbols),
        "missing_symbols": len(set(symbols) - loaded_symbols),
        "rows": int(len(data)),
        **universe_diag,
    }
    return PriceLoadResult(prices=data, diagnostics=diagnostics)


def _resolve_symbols(
    request: dict[str, Any],
    store: ParquetStore,
    adjust: str,
) -> tuple[list[str], dict[str, Any]]:
    universe_id = str(request.get("universe_id") or "").strip()
    pit_csv = request.get("pit_csv") or _default_pit_csv(universe_id)
    if pit_csv and universe_id:
        provider = PointInTimeUniverseProvider.from_csv(str(pit_csv))
        universe = provider.get_universe(universe_id)
        return list(universe.symbols), {
            "universe_id": universe_id,
            "universe_source": "pit_csv",
            "pit_csv": str(pit_csv),
            "quality": "strict_historical_pit",
        }

    if universe_id:
        try:
            universe = get_builtin_universe_provider().get_universe(universe_id)
            return list(universe.symbols), {
                "universe_id": universe_id,
                "universe_source": "builtin_static",
                "quality": "best_effort_static",
            }
        except UniverseNotFoundError as exc:
            raise ValueError(
                f"本地缓存价格源无法解析股票池 {universe_id}；"
                "请提供 PIT CSV、使用内置股票池，或改用 price_csv"
            ) from exc

    symbols = store.list_symbols(adjust=adjust)
    if not symbols:
        raise ValueError(f"本地数据目录没有 {adjust} 行情文件: {store}")
    return symbols, {
        "universe_id": None,
        "universe_source": "local_cache_all_symbols",
        "quality": "local_downloaded_cache",
    }


def _data_root(request: dict[str, Any]) -> Path:
    requested = request.get("data_root") or os.getenv("CQ_DATA_ROOT")
    if requested:
        return Path(str(requested)).expanduser()
    return Config.from_yaml("config/default.yaml").data.root_path


def _default_pit_csv(universe_id: str) -> Path | None:
    if not universe_id.upper().endswith("_PIT"):
        return None
    path = Path("data/universes/pit_memberships.csv")
    return path if path.exists() else None


def _optional_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    return pd.Timestamp(value).date()


def _require_columns(frame: pd.DataFrame, columns: list[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"prices missing required columns: {', '.join(missing)}")
