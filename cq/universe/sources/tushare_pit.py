"""Tushare Pro index-weight downloader for PIT A-share universes."""

from __future__ import annotations

import calendar
import time
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


class TushareIndexWeightClient(Protocol):
    """Subset of the Tushare Pro client used by this module."""

    def index_weight(self, **kwargs: Any) -> pd.DataFrame:
        """Return Tushare index_weight rows."""


@dataclass(frozen=True)
class PitIndexSpec:
    """Index mapping and validation settings for a PIT universe."""

    universe_id: str
    index_code: str
    name: str
    min_symbols: int


@dataclass(frozen=True)
class TusharePitFetchResult:
    """Files and data frames produced by a Tushare PIT fetch."""

    output_csv: Path
    weights_output: Path
    raw_files: list[Path]
    memberships: pd.DataFrame
    weights: pd.DataFrame
    validation_result: PitValidationResult
    validation_export: PitValidationExport
    summary: dict[str, Any]


class TusharePitSourceError(RuntimeError):
    """Raised when Tushare PIT data cannot be fetched or normalized."""


DEFAULT_TUSHARE_INDEXES: tuple[PitIndexSpec, ...] = (
    PitIndexSpec("HS300_PIT", "399300.SZ", "沪深300", 250),
    PitIndexSpec("ZZ500_PIT", "000905.SH", "中证500", 400),
    PitIndexSpec("ZZ1000_PIT", "000852.SH", "中证1000", 800),
)
_DEFAULT_BY_UNIVERSE = {spec.universe_id.lower(): spec for spec in DEFAULT_TUSHARE_INDEXES}
_TUSHARE_COLUMNS = ["index_code", "con_code", "trade_date", "weight"]


def fetch_tushare_pit_universe(
    *,
    start: date,
    end: date,
    output_csv: str | Path = "data/universes/pit_memberships.csv",
    weights_output: str | Path = "data/universes/pit_weights.csv",
    raw_dir: str | Path = "data/raw/tushare/index_weight",
    validation_dir: str | Path = "output/universe_validation",
    index_specs: Iterable[PitIndexSpec] | None = None,
    token: str | None = None,
    client: TushareIndexWeightClient | None = None,
    min_symbols: int | Mapping[str, int] | None = None,
    request_pause: float = 0.0,
) -> TusharePitFetchResult:
    """Fetch Tushare index weights and export project-standard PIT universes."""

    if start > end:
        raise ValueError("start must be <= end")

    specs = tuple(index_specs or DEFAULT_TUSHARE_INDEXES)
    if not specs:
        raise ValueError("at least one index spec is required")

    pro = client or create_tushare_client(token)
    output_path = Path(output_csv)
    weights_path = Path(weights_output)
    raw_path = Path(raw_dir)
    raw_files: list[Path] = []
    frames: list[pd.DataFrame] = []

    for spec in specs:
        for month_start, month_end in iter_month_ranges(start, end):
            raw = _fetch_month(pro, spec, month_start, month_end)
            raw_file = _write_raw_month(raw, raw_path, spec, month_start)
            raw_files.append(raw_file)
            if not raw.empty:
                frames.append(_annotate_raw_frame(raw, spec))
            if request_pause > 0:
                time.sleep(request_pause)

    combined = pd.concat(frames, ignore_index=True) if frames else _empty_weight_frame()
    weights = normalize_tushare_weights(combined)
    memberships = compress_tushare_weight_snapshots(weights, specs, initial_start=start)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    _to_export_dates(memberships).to_csv(output_path, index=False)
    _to_export_dates(weights, date_columns=("trade_date",)).to_csv(weights_path, index=False)

    validation_thresholds = _validation_thresholds(specs, min_symbols)
    validation = validate_pit_memberships(
        memberships,
        expected_universes=[spec.universe_id for spec in specs],
        min_symbols=validation_thresholds,
        coverage_start=start,
        coverage_end=end,
    )
    snapshot_dates = _snapshot_date_ranges(weights)
    summary = {
        "provider": "tushare",
        "source_quality": "strict_historical_index_weight",
        "source_warning": "Tushare index_weight 可构建历史 PIT，但依赖账号权限和接口可用性。",
        "strict_historical_pit": True,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "index_count": len(specs),
        "raw_file_count": len(raw_files),
        "weight_rows": int(len(weights)),
        "membership_rows": int(len(memberships)),
        "snapshot_dates": snapshot_dates,
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
    return TusharePitFetchResult(
        output_csv=output_path,
        weights_output=weights_path,
        raw_files=raw_files,
        memberships=memberships,
        weights=weights,
        validation_result=validation,
        validation_export=validation_export,
        summary=summary,
    )


def create_tushare_client(token: str | None = None) -> TushareIndexWeightClient:
    """Create a Tushare Pro client, keeping token handling outside the repo."""

    if not token:
        raise TusharePitSourceError(
            "缺少 TUSHARE_TOKEN。请设置环境变量或 config/local.yaml:data.tushare_token；"
            "若暂时没有权限，可使用 cq import-pit-universe 手工导入 CSV。"
        )
    try:
        import tushare as ts  # type: ignore[import-untyped]
    except ImportError as exc:
        raise TusharePitSourceError(
            "未安装 tushare。请运行 pip install -e '.[tushare]'，或使用 cq import-pit-universe 手工导入 CSV。"
        ) from exc
    try:
        return ts.pro_api(token)
    except Exception as exc:
        raise TusharePitSourceError("初始化 Tushare Pro 客户端失败，请检查 TUSHARE_TOKEN。") from exc


def parse_tushare_index_specs(items: Iterable[str]) -> tuple[PitIndexSpec, ...]:
    """Parse CLI ``UNIVERSE_ID=INDEX_CODE`` mappings."""

    values = tuple(items)
    if not values:
        return DEFAULT_TUSHARE_INDEXES

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


def iter_month_ranges(start: date, end: date) -> Iterable[tuple[date, date]]:
    """Yield inclusive month ranges clipped to ``start`` and ``end``."""

    current = date(start.year, start.month, 1)
    while current <= end:
        last_day = calendar.monthrange(current.year, current.month)[1]
        month_start = max(start, current)
        month_end = min(end, date(current.year, current.month, last_day))
        yield month_start, month_end
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def normalize_tushare_weights(weights: pd.DataFrame) -> pd.DataFrame:
    """Normalize annotated Tushare index_weight rows."""

    columns = ["universe_id", "symbol", "trade_date", "weight"]
    if weights.empty:
        return pd.DataFrame(columns=columns)

    missing = [column for column in columns if column not in weights.columns]
    if missing:
        raise ValueError(f"tushare weights missing required columns: {', '.join(missing)}")

    data = weights[columns].copy()
    data["universe_id"] = data["universe_id"].astype(str).str.strip().str.lower()
    data["symbol"] = data["symbol"].astype(str).str.strip().str.upper()
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="coerce").dt.normalize()
    data["weight"] = pd.to_numeric(data["weight"], errors="coerce")
    invalid = data["universe_id"].eq("") | data["symbol"].eq("") | data["trade_date"].isna()
    if invalid.any():
        raise ValueError(f"tushare weights contains {int(invalid.sum())} invalid rows")
    return (
        data.dropna(subset=["weight"])
        .drop_duplicates(["universe_id", "symbol", "trade_date"], keep="last")
        .sort_values(["universe_id", "trade_date", "symbol"])
        .reset_index(drop=True)
    )


def compress_tushare_weight_snapshots(
    weights: pd.DataFrame,
    index_specs: Iterable[PitIndexSpec] | None = None,
    *,
    initial_start: date | None = None,
) -> pd.DataFrame:
    """Compress monthly Tushare weight snapshots into PIT membership intervals."""

    specs_by_id = {
        spec.universe_id.lower(): spec
        for spec in tuple(index_specs or DEFAULT_TUSHARE_INDEXES)
    }
    normalized = normalize_tushare_weights(weights)
    rows: list[dict[str, Any]] = []
    if normalized.empty:
        return pd.DataFrame(columns=["universe_id", "symbol", "start_date", "end_date", "name"])

    for universe_id, group in normalized.groupby("universe_id", sort=True):
        name = specs_by_id.get(str(universe_id), PitIndexSpec(str(universe_id), "", str(universe_id).upper(), 1)).name
        active: dict[str, pd.Timestamp] = {}
        for i, (snapshot_date, snapshot) in enumerate(group.groupby("trade_date", sort=True)):
            current = set(snapshot["symbol"].astype(str))
            for symbol in sorted(set(active) - current):
                rows.append(
                    {
                        "universe_id": universe_id,
                        "symbol": symbol,
                        "start_date": active.pop(symbol),
                        "end_date": pd.Timestamp(snapshot_date) - pd.Timedelta(days=1),
                        "name": name,
                    }
                )
            for symbol in sorted(current - set(active)):
                active[symbol] = _effective_interval_start(pd.Timestamp(snapshot_date), i, initial_start)

        for symbol, start_date in sorted(active.items()):
            rows.append(
                {
                    "universe_id": universe_id,
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": pd.NaT,
                    "name": name,
                }
            )

    return normalize_pit_memberships(pd.DataFrame(rows))


def _effective_interval_start(snapshot_date: pd.Timestamp, snapshot_index: int, initial_start: date | None) -> pd.Timestamp:
    if snapshot_index == 0 and initial_start is not None:
        requested = pd.Timestamp(initial_start)
        if requested <= snapshot_date and requested.to_period("M") == snapshot_date.to_period("M"):
            return requested
    return snapshot_date


def _fetch_month(
    client: TushareIndexWeightClient,
    spec: PitIndexSpec,
    month_start: date,
    month_end: date,
) -> pd.DataFrame:
    try:
        raw = client.index_weight(
            index_code=spec.index_code,
            start_date=month_start.strftime("%Y%m%d"),
            end_date=month_end.strftime("%Y%m%d"),
        )
    except Exception as exc:
        raise TusharePitSourceError(
            f"Tushare index_weight 下载失败: {spec.index_code} {month_start:%Y-%m}。"
            "请确认 TUSHARE_TOKEN 权限，或使用 cq import-pit-universe 手工导入 CSV。"
        ) from exc
    if raw is None:
        return pd.DataFrame(columns=_TUSHARE_COLUMNS)
    return raw.copy()


def _write_raw_month(
    raw: pd.DataFrame,
    raw_dir: Path,
    spec: PitIndexSpec,
    month_start: date,
) -> Path:
    path = raw_dir / spec.index_code / f"{month_start:%Y%m}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = raw.copy()
    for column in _TUSHARE_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")
    frame.to_csv(path, index=False)
    return path


def _annotate_raw_frame(raw: pd.DataFrame, spec: PitIndexSpec) -> pd.DataFrame:
    required = ["con_code", "trade_date", "weight"]
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise ValueError(f"Tushare index_weight missing required columns: {', '.join(missing)}")
    data = raw.copy()
    data["universe_id"] = spec.universe_id
    data["symbol"] = data["con_code"]
    return data[["universe_id", "symbol", "trade_date", "weight"]]


def _empty_weight_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["universe_id", "symbol", "trade_date", "weight"])


def _validation_thresholds(
    specs: tuple[PitIndexSpec, ...],
    override: int | Mapping[str, int] | None,
) -> int | dict[str, int]:
    if isinstance(override, int):
        return override
    if override is not None:
        return {str(key).lower(): int(value) for key, value in override.items()}
    return {spec.universe_id.lower(): spec.min_symbols for spec in specs}


def _snapshot_date_ranges(weights: pd.DataFrame) -> dict[str, str]:
    if weights.empty:
        return {}
    ranges: dict[str, str] = {}
    for universe_id, group in weights.groupby("universe_id", sort=True):
        dates = pd.to_datetime(group["trade_date"], errors="coerce").dropna()
        if dates.empty:
            continue
        ranges[str(universe_id)] = f"{dates.min().date().isoformat()} 至 {dates.max().date().isoformat()}"
    return ranges


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
