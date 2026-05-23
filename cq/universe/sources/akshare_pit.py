"""AkShare CSIndex constituent downloader for free PIT universe bootstrapping.

AkShare's CSIndex endpoints expose the latest public constituent snapshot and
weights. This is useful for free local bootstrapping, but it is not a complete
historical point-in-time membership source.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from cq.universe.pit import (
    PitValidationExport,
    PitValidationResult,
    export_pit_validation_result,
    normalize_pit_memberships,
    validate_pit_memberships,
)
from cq.universe.sources.common import write_pit_fetch_artifacts
from cq.universe.sources.tushare_pit import PitIndexSpec


class AksharePitClient(Protocol):
    """Subset of the AkShare module used by this adapter."""

    def index_stock_cons_weight_csindex(self, symbol: str) -> pd.DataFrame:
        """Return latest CSIndex constituent weight rows."""

    def index_stock_cons_csindex(self, symbol: str) -> pd.DataFrame:
        """Return latest CSIndex constituent rows."""


@dataclass(frozen=True)
class AksharePitFetchResult:
    """Files and data frames produced by an AkShare PIT fetch."""

    output_csv: Path
    weights_output: Path
    raw_files: list[Path]
    memberships: pd.DataFrame
    weights: pd.DataFrame
    validation_result: PitValidationResult
    validation_export: PitValidationExport
    summary: dict[str, Any]


class AksharePitSourceError(RuntimeError):
    """Raised when AkShare PIT data cannot be fetched or normalized."""


DEFAULT_AKSHARE_INDEXES: tuple[PitIndexSpec, ...] = (
    PitIndexSpec("HS300_PIT", "000300", "沪深300", 250),
    PitIndexSpec("ZZ500_PIT", "000905", "中证500", 400),
    PitIndexSpec("ZZ1000_PIT", "000852", "中证1000", 800),
)
_DEFAULT_BY_UNIVERSE = {spec.universe_id.lower(): spec for spec in DEFAULT_AKSHARE_INDEXES}
_WEIGHT_ENDPOINT = "index_stock_cons_weight_csindex"
_CONS_ENDPOINT = "index_stock_cons_csindex"
_AKSHARE_WEIGHT_COLUMNS = ["universe_id", "symbol", "trade_date", "weight"]


def fetch_akshare_pit_universe(
    *,
    start: date,
    end: date,
    output_csv: str | Path = "data/universes/pit_memberships.csv",
    weights_output: str | Path = "data/universes/pit_weights.csv",
    raw_dir: str | Path = "data/raw/akshare/csindex",
    validation_dir: str | Path = "output/universe_validation",
    index_specs: Iterable[PitIndexSpec] | None = None,
    client: AksharePitClient | None = None,
    min_symbols: int | Mapping[str, int] | None = None,
) -> AksharePitFetchResult:
    """Fetch free AkShare CSIndex snapshots and export project PIT CSV files."""

    if start > end:
        raise ValueError("start must be <= end")

    specs = tuple(index_specs or DEFAULT_AKSHARE_INDEXES)
    if not specs:
        raise ValueError("at least one index spec is required")

    ak_client = client or create_akshare_client()
    output_path = Path(output_csv)
    weights_path = Path(weights_output)
    raw_path = Path(raw_dir)
    raw_files: list[Path] = []
    frames: list[pd.DataFrame] = []
    endpoints: dict[str, str] = {}

    for spec in specs:
        raw, endpoint = _fetch_latest_snapshot(ak_client, spec)
        normalized = normalize_akshare_snapshot(raw, spec)
        snapshot_date = _latest_snapshot_date(normalized)
        raw_files.append(_write_raw_snapshot(raw, raw_path, spec, snapshot_date, endpoint))
        frames.append(normalized)
        endpoints[spec.universe_id.lower()] = endpoint

    weights = normalize_akshare_weights(pd.concat(frames, ignore_index=True))
    memberships = compress_akshare_latest_snapshots(weights, specs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    _to_export_dates(memberships).to_csv(output_path, index=False)
    _to_export_dates(weights, date_columns=("trade_date",)).to_csv(weights_path, index=False)

    validation_thresholds = _validation_thresholds(specs, min_symbols)
    effective_start = _effective_coverage_start(memberships, requested_start=start)
    validation = validate_pit_memberships(
        memberships,
        expected_universes=[spec.universe_id for spec in specs],
        min_symbols=validation_thresholds,
        coverage_start=effective_start,
        coverage_end=end,
    )
    snapshot_dates = {
        str(universe_id): _date_str(group["trade_date"].max())
        for universe_id, group in weights.groupby("universe_id", sort=True)
    }
    summary = {
        "provider": "akshare",
        "source_quality": "free_best_effort_latest_snapshot",
        "source_warning": "AkShare 免费源只提供公开最新快照，不代表严格历史 PIT 股票池。",
        "strict_historical_pit": False,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "effective_coverage_start": effective_start.isoformat(),
        "end": end.isoformat(),
        "index_count": len(specs),
        "raw_file_count": len(raw_files),
        "weight_rows": int(len(weights)),
        "membership_rows": int(len(memberships)),
        "snapshot_dates": snapshot_dates,
        "endpoints": endpoints,
        "output_csv": str(output_path),
        "weights_output": str(weights_path),
        "validation_passed": validation.passed,
        "validation_dir": str(validation_dir),
    }
    validation_export = export_pit_validation_result(validation, validation_dir)
    sidecar_summary_path = output_path.with_suffix(".summary.json")
    summary["validation_dir"] = str(validation_export.output_dir)
    summary.update(
        write_pit_fetch_artifacts(
            summary,
            validation_dir=validation_export.output_dir,
            sidecar_summary_path=sidecar_summary_path,
        )
    )
    return AksharePitFetchResult(
        output_csv=output_path,
        weights_output=weights_path,
        raw_files=raw_files,
        memberships=memberships,
        weights=weights,
        validation_result=validation,
        validation_export=validation_export,
        summary=summary,
    )


def create_akshare_client() -> AksharePitClient:
    """Return the AkShare module as a lightweight client."""

    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError as exc:
        raise AksharePitSourceError("未安装 akshare。请运行 pip install -e '.[dev]' 或 pip install akshare。") from exc
    return ak


def parse_akshare_index_specs(items: Iterable[str]) -> tuple[PitIndexSpec, ...]:
    """Parse CLI ``UNIVERSE_ID=INDEX_CODE`` mappings for AkShare CSIndex APIs."""

    values = tuple(items)
    if not values:
        return DEFAULT_AKSHARE_INDEXES

    specs: list[PitIndexSpec] = []
    seen: set[str] = set()
    for item in values:
        if "=" not in item:
            raise ValueError(f"index mapping must be UNIVERSE_ID=INDEX_CODE: {item}")
        universe_id, index_code = (part.strip() for part in item.split("=", 1))
        if not universe_id or not index_code:
            raise ValueError(f"index mapping must be UNIVERSE_ID=INDEX_CODE: {item}")
        normalized = universe_id.lower()
        if normalized in seen:
            raise ValueError(f"duplicate universe id: {universe_id}")
        seen.add(normalized)
        default = _DEFAULT_BY_UNIVERSE.get(normalized)
        specs.append(
            PitIndexSpec(
                universe_id=universe_id,
                index_code=index_code.upper(),
                name=default.name if default else universe_id.upper(),
                min_symbols=default.min_symbols if default else 1,
            )
        )
    return tuple(specs)


def normalize_akshare_snapshot(raw: pd.DataFrame, spec: PitIndexSpec) -> pd.DataFrame:
    """Normalize one AkShare CSIndex snapshot into weight-like rows."""

    required = ["日期", "成分券代码"]
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise ValueError(f"AkShare CSIndex snapshot missing required columns: {', '.join(missing)}")
    if raw.empty:
        raise AksharePitSourceError(f"AkShare CSIndex snapshot is empty: {spec.index_code}")

    data = raw.copy()
    trade_dates = pd.to_datetime(data["日期"], errors="coerce").dt.normalize()
    symbols = [
        _normalize_symbol(code, exchange)
        for code, exchange in zip(
            data["成分券代码"],
            data["交易所"] if "交易所" in data.columns else [pd.NA] * len(data),
            strict=True,
        )
    ]
    weight_values = pd.to_numeric(data["权重"], errors="coerce") if "权重" in data.columns else pd.Series(pd.NA, index=data.index)

    normalized = pd.DataFrame(
        {
            "universe_id": spec.universe_id,
            "symbol": symbols,
            "trade_date": trade_dates,
            "weight": weight_values,
        }
    )
    invalid = normalized["trade_date"].isna() | normalized["symbol"].eq("")
    if invalid.any():
        raise ValueError(f"AkShare CSIndex snapshot contains {int(invalid.sum())} invalid rows")
    return normalized


def normalize_akshare_weights(weights: pd.DataFrame) -> pd.DataFrame:
    """Normalize combined AkShare snapshot rows without requiring weights."""

    if weights.empty:
        return pd.DataFrame(columns=_AKSHARE_WEIGHT_COLUMNS)

    missing = [column for column in _AKSHARE_WEIGHT_COLUMNS if column not in weights.columns]
    if missing:
        raise ValueError(f"AkShare weights missing required columns: {', '.join(missing)}")

    data = weights[_AKSHARE_WEIGHT_COLUMNS].copy()
    data["universe_id"] = data["universe_id"].astype(str).str.strip().str.lower()
    data["symbol"] = data["symbol"].astype(str).str.strip().str.upper()
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="coerce").dt.normalize()
    data["weight"] = pd.to_numeric(data["weight"], errors="coerce")
    invalid = data["universe_id"].eq("") | data["symbol"].eq("") | data["trade_date"].isna()
    if invalid.any():
        raise ValueError(f"AkShare weights contains {int(invalid.sum())} invalid rows")
    return (
        data.drop_duplicates(["universe_id", "symbol", "trade_date"], keep="last")
        .sort_values(["universe_id", "trade_date", "symbol"])
        .reset_index(drop=True)
    )


def compress_akshare_latest_snapshots(
    weights: pd.DataFrame,
    index_specs: Iterable[PitIndexSpec] | None = None,
) -> pd.DataFrame:
    """Convert latest AkShare snapshots into open-ended membership intervals."""

    specs_by_id = {
        spec.universe_id.lower(): spec
        for spec in tuple(index_specs or DEFAULT_AKSHARE_INDEXES)
    }
    normalized = normalize_akshare_weights(weights)
    rows: list[dict[str, Any]] = []
    if normalized.empty:
        return pd.DataFrame(columns=["universe_id", "symbol", "start_date", "end_date", "name"])

    for universe_id, group in normalized.groupby("universe_id", sort=True):
        snapshot_date = group["trade_date"].max()
        snapshot = group[group["trade_date"] == snapshot_date]
        fallback = PitIndexSpec(str(universe_id), "", str(universe_id).upper(), 1)
        name = specs_by_id.get(str(universe_id), fallback).name
        for symbol in sorted(snapshot["symbol"].drop_duplicates()):
            rows.append(
                {
                    "universe_id": universe_id,
                    "symbol": symbol,
                    "start_date": snapshot_date,
                    "end_date": pd.NaT,
                    "name": name,
                }
            )

    return normalize_pit_memberships(pd.DataFrame(rows))


def _fetch_latest_snapshot(
    client: AksharePitClient,
    spec: PitIndexSpec,
) -> tuple[pd.DataFrame, str]:
    first_error: Exception | None = None
    try:
        raw = client.index_stock_cons_weight_csindex(symbol=spec.index_code)
    except Exception as exc:
        first_error = exc
    else:
        if raw is not None and not raw.empty:
            return raw.copy(), _WEIGHT_ENDPOINT

    try:
        raw = client.index_stock_cons_csindex(symbol=spec.index_code)
    except Exception as exc:
        message = f"AkShare CSIndex 下载失败: {spec.index_code}。请稍后重试或使用 cq import-pit-universe 手工导入 CSV。"
        raise AksharePitSourceError(message) from (first_error or exc)

    if raw is not None and not raw.empty:
        return raw.copy(), _CONS_ENDPOINT

    if first_error is not None:
        raise AksharePitSourceError(
            f"AkShare CSIndex 权重接口失败且成分接口返回空数据: {spec.index_code}。"
        ) from first_error
    raise AksharePitSourceError(f"AkShare CSIndex 返回空数据: {spec.index_code}")


def _write_raw_snapshot(
    raw: pd.DataFrame,
    raw_dir: Path,
    spec: PitIndexSpec,
    snapshot_date: pd.Timestamp,
    endpoint: str,
) -> Path:
    path = raw_dir / endpoint / spec.index_code / f"{snapshot_date:%Y%m%d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(path, index=False)
    return path


def _latest_snapshot_date(weights: pd.DataFrame) -> pd.Timestamp:
    trade_dates = pd.to_datetime(weights["trade_date"], errors="coerce")
    if trade_dates.isna().all():
        raise ValueError("AkShare snapshot has no valid trade_date")
    return pd.Timestamp(trade_dates.max()).normalize()


def _normalize_symbol(code: Any, exchange: Any) -> str:
    raw = str(code).strip().upper()
    if not raw:
        return ""
    if "." in raw:
        base, suffix = raw.split(".", 1)
        return f"{base.zfill(6)}.{suffix}"

    base = raw.zfill(6)
    suffix = _exchange_suffix(exchange) or _infer_exchange_suffix(base)
    return f"{base}.{suffix}"


def _exchange_suffix(exchange: Any) -> str | None:
    text = "" if pd.isna(exchange) else str(exchange).strip().upper()
    if not text:
        return None
    if "上海" in text or text in {"SH", "SSE", "XSHG"}:
        return "SH"
    if "深圳" in text or text in {"SZ", "SZSE", "XSHE"}:
        return "SZ"
    if "北京" in text or "北交" in text or text in {"BJ", "BSE"}:
        return "BJ"
    return None


def _infer_exchange_suffix(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "SH"
    if symbol.startswith(("0", "2", "3")):
        return "SZ"
    if symbol.startswith(("4", "8")):
        return "BJ"
    raise ValueError(f"cannot infer exchange suffix for symbol: {symbol}")


def _effective_coverage_start(memberships: pd.DataFrame, *, requested_start: date) -> date:
    if memberships.empty:
        return requested_start
    common_start = pd.to_datetime(memberships["start_date"], errors="coerce").max()
    if pd.isna(common_start):
        return requested_start
    return pd.Timestamp(common_start).date()


def _validation_thresholds(
    specs: tuple[PitIndexSpec, ...],
    override: int | Mapping[str, int] | None,
) -> int | dict[str, int]:
    if isinstance(override, int):
        return override
    if override is not None:
        return {str(key).lower(): int(value) for key, value in override.items()}
    return {spec.universe_id.lower(): spec.min_symbols for spec in specs}


def _to_export_dates(
    df: pd.DataFrame,
    *,
    date_columns: tuple[str, ...] = ("start_date", "end_date"),
) -> pd.DataFrame:
    data = df.copy()
    for column in date_columns:
        if column not in data.columns:
            continue
        data[column] = pd.to_datetime(data[column], errors="coerce").dt.strftime("%Y-%m-%d")
        data[column] = data[column].replace("NaT", "").fillna("")
    return data


def _date_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()
