"""Point-in-time universe provider and import helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from cq.universe.base import Universe, UniverseNotFoundError

PIT_MEMBERSHIP_COLUMNS = ("universe_id", "symbol", "start_date", "end_date", "name")
PIT_SCHEMA_VERSION = "pit_membership.v1"
PIT_VALIDATION_SCHEMA_VERSION = "pit_validation.v1"


@dataclass(frozen=True)
class PitImportResult:
    """Files and diagnostics produced by PIT membership import."""

    output_csv: Path
    diagnostics_path: Path | None
    summary: dict[str, Any]


@dataclass(frozen=True)
class PitValidationIssue:
    """A machine-readable PIT membership validation issue."""

    severity: str
    code: str
    message: str
    universe_id: str | None = None
    symbol: str | None = None
    date: str | None = None


@dataclass(frozen=True)
class PitValidationResult:
    """PIT validation summary, issues, and Markdown report."""

    summary: dict[str, Any]
    issues: list[PitValidationIssue]
    markdown: str

    @property
    def passed(self) -> bool:
        return bool(self.summary.get("passed", False))


@dataclass(frozen=True)
class PitValidationExport:
    """Files written by PIT validation export."""

    output_dir: Path
    files: dict[str, Path]
    summary: dict[str, Any]


class PointInTimeUniverseProvider:
    """Resolve historical universe membership by effective dates."""

    def __init__(
        self,
        memberships: pd.DataFrame | Iterable[Mapping[str, Any]],
        *,
        universe_id_col: str = "universe_id",
        symbol_col: str = "symbol",
        start_date_col: str = "start_date",
        end_date_col: str = "end_date",
        name_col: str = "name",
    ) -> None:
        self._universe_id_col = "universe_id"
        self._symbol_col = "symbol"
        self._start_date_col = "start_date"
        self._end_date_col = "end_date"
        self._name_col = "name"
        self._memberships = self._normalize_memberships(
            pd.DataFrame(memberships),
            required=[universe_id_col, symbol_col, start_date_col],
            universe_id_col=universe_id_col,
            symbol_col=symbol_col,
            start_date_col=start_date_col,
            end_date_col=end_date_col,
            name_col=name_col,
        )

    @classmethod
    def from_csv(cls, path: str | Path, **kwargs: Any) -> PointInTimeUniverseProvider:
        return cls(pd.read_csv(path), **kwargs)

    def list_universes(self) -> list[Universe]:
        universes: list[Universe] = []
        for universe_id, group in self._memberships.groupby(self._universe_id_col, sort=True):
            universes.append(self._build_universe(str(universe_id), group))
        return universes

    def get_universe(self, universe_id: str) -> Universe:
        normalized = _normalize_universe_id(universe_id)
        group = self._memberships[self._memberships[self._universe_id_col] == normalized]
        if group.empty:
            raise UniverseNotFoundError(f"unknown universe: {universe_id}")
        return self._build_universe(normalized, group)

    def get_symbols(self, universe_id: str, trade_date: date | None = None) -> list[str]:
        if trade_date is None:
            raise ValueError("trade_date is required for point-in-time universe")
        normalized = _normalize_universe_id(universe_id)
        self.get_universe(normalized)
        target = pd.Timestamp(trade_date).normalize()
        group = self._memberships[self._memberships[self._universe_id_col] == normalized]
        mask = (group[self._start_date_col] <= target) & (
            group[self._end_date_col].isna() | (group[self._end_date_col] >= target)
        )
        symbols = group.loc[mask, self._symbol_col].drop_duplicates().sort_values()
        return symbols.tolist()

    def _build_universe(self, universe_id: str, group: pd.DataFrame) -> Universe:
        name = universe_id.upper()
        if self._name_col in group.columns:
            names = group[self._name_col].dropna().astype(str)
            if not names.empty:
                name = names.iloc[0]
        symbols = tuple(group[self._symbol_col].drop_duplicates().sort_values().tolist())
        return Universe(
            id=universe_id,
            name=name,
            source="point_in_time_membership",
            construction="point_in_time",
            description="按历史生效区间解析的 point-in-time 股票池。",
            symbols=symbols,
            metadata={
                "member_count": len(symbols),
                "start_date": _date_str(group[self._start_date_col].min()),
                "end_date": _date_str(group[self._end_date_col].dropna().max()),
            },
        )

    def _normalize_memberships(
        self,
        df: pd.DataFrame,
        *,
        required: list[str],
        universe_id_col: str,
        symbol_col: str,
        start_date_col: str,
        end_date_col: str,
        name_col: str,
    ) -> pd.DataFrame:
        data = normalize_pit_memberships(
            df,
            universe_id_col=universe_id_col,
            symbol_col=symbol_col,
            start_date_col=start_date_col,
            end_date_col=end_date_col,
            name_col=name_col,
            required=required,
        )
        data = data.drop_duplicates(
            [self._universe_id_col, self._symbol_col, self._start_date_col, self._end_date_col]
        )
        return data.sort_values([self._universe_id_col, self._start_date_col, self._symbol_col]).reset_index(drop=True)


def normalize_pit_memberships(
    memberships: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    universe_id_col: str = "universe_id",
    symbol_col: str = "symbol",
    start_date_col: str = "start_date",
    end_date_col: str = "end_date",
    name_col: str = "name",
    required: list[str] | None = None,
) -> pd.DataFrame:
    """Normalize external PIT membership data into the canonical contract."""

    df = pd.DataFrame(memberships).copy()
    required_columns = required or [universe_id_col, symbol_col, start_date_col]
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise ValueError(f"memberships missing required columns: {', '.join(missing)}")

    if end_date_col not in df.columns:
        df[end_date_col] = pd.NA
    if name_col not in df.columns:
        df[name_col] = ""

    data = df[[universe_id_col, symbol_col, start_date_col, end_date_col, name_col]].copy()
    data[universe_id_col] = data[universe_id_col].astype(str).str.strip().map(_normalize_universe_id)
    data[symbol_col] = data[symbol_col].astype(str).str.strip().str.upper()
    data[name_col] = data[name_col].fillna("").astype(str).str.strip()
    data[start_date_col] = pd.to_datetime(data[start_date_col], errors="coerce").dt.normalize()
    data[end_date_col] = pd.to_datetime(data[end_date_col], errors="coerce").dt.normalize()

    invalid = data[universe_id_col].eq("") | data[symbol_col].eq("") | data[start_date_col].isna()
    if invalid.any():
        raise ValueError(f"memberships contains {int(invalid.sum())} invalid required rows")

    reversed_range = data[end_date_col].notna() & (data[end_date_col] < data[start_date_col])
    if reversed_range.any():
        raise ValueError(f"memberships contains {int(reversed_range.sum())} rows with end_date before start_date")

    return (
        data.rename(
            columns={
                universe_id_col: "universe_id",
                symbol_col: "symbol",
                start_date_col: "start_date",
                end_date_col: "end_date",
                name_col: "name",
            }
        )
        .drop_duplicates(["universe_id", "symbol", "start_date", "end_date"])
        .sort_values(["universe_id", "start_date", "symbol"])
        .reset_index(drop=True)
    )


def import_pit_memberships(
    input_csv: str | Path,
    output_csv: str | Path,
    *,
    diagnostics_path: str | Path | None = None,
    encoding: str = "utf-8",
) -> PitImportResult:
    """Read an external PIT CSV, normalize it, and write the canonical CSV."""

    input_path = Path(input_csv)
    output_path = Path(output_csv)
    data = normalize_pit_memberships(pd.read_csv(input_path, encoding=encoding))
    export_data = data.copy()
    for column in ("start_date", "end_date"):
        export_data[column] = pd.to_datetime(export_data[column]).dt.strftime("%Y-%m-%d")
    export_data["end_date"] = export_data["end_date"].replace("NaT", "")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    export_data.to_csv(output_path, index=False)

    summary = pit_membership_diagnostics(data)
    summary["schema_version"] = PIT_SCHEMA_VERSION
    summary["input_csv"] = str(input_path)
    summary["output_csv"] = str(output_path)

    diag_path = Path(diagnostics_path) if diagnostics_path else None
    if diag_path is not None:
        diag_path.parent.mkdir(parents=True, exist_ok=True)
        diag_path.write_text(json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return PitImportResult(output_csv=output_path, diagnostics_path=diag_path, summary=summary)


def validate_pit_memberships(
    memberships: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    expected_universes: Iterable[str] | None = None,
    min_symbols: int | Mapping[str, int] | None = None,
    coverage_start: date | str | None = None,
    coverage_end: date | str | None = None,
) -> PitValidationResult:
    """Validate PIT memberships before using them in benchmark/backtest runs."""

    data = normalize_pit_memberships(memberships)
    issues: list[PitValidationIssue] = []
    diagnostics = pit_membership_diagnostics(data)
    expected = sorted({_normalize_universe_id(item) for item in expected_universes or []})
    actual = set(data["universe_id"].unique())

    for universe_id in expected:
        if universe_id not in actual:
            issues.append(
                PitValidationIssue(
                    severity="error",
                    code="missing_expected_universe",
                    message=f"缺少预期 PIT 股票池: {universe_id}",
                    universe_id=universe_id,
                )
            )

    _check_symbol_format(data, issues)
    _check_interval_overlaps(data, issues)
    if min_symbols is not None:
        _check_min_symbols(data, min_symbols, issues)

    coverage_dates = [_coerce_date_or_none(coverage_start), _coerce_date_or_none(coverage_end)]
    coverage_dates = [item for item in coverage_dates if item is not None]
    if coverage_dates:
        _check_coverage_dates(data, coverage_dates, min_symbols, issues)

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    summary = {
        "schema_version": PIT_VALIDATION_SCHEMA_VERSION,
        "passed": error_count == 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "expected_universes": expected,
        "actual_universes": sorted(actual),
        "coverage_start": coverage_dates[0].isoformat() if coverage_dates else None,
        "coverage_end": coverage_dates[-1].isoformat() if coverage_dates else None,
        "min_symbols": min_symbols,
        "diagnostics": diagnostics,
    }
    markdown = generate_pit_validation_report(summary, issues)
    return PitValidationResult(summary=summary, issues=issues, markdown=markdown)


def export_pit_validation_result(
    result: PitValidationResult,
    output_dir: str | Path,
) -> PitValidationExport:
    """Export PIT validation summary, issues, and Markdown report."""

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    summary_path = out / "pit_validation_summary.json"
    summary_path.write_text(
        json.dumps(_json_safe(result.summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files["summary"] = summary_path

    issues_path = out / "pit_validation_issues.csv"
    issue_columns = ["severity", "code", "message", "universe_id", "symbol", "date"]
    pd.DataFrame([asdict(issue) for issue in result.issues], columns=issue_columns).to_csv(issues_path, index=False)
    files["issues"] = issues_path

    report_path = out / "pit_validation_report.md"
    report_path.write_text(result.markdown, encoding="utf-8")
    files["report"] = report_path
    return PitValidationExport(output_dir=out, files=files, summary=result.summary)


def generate_pit_validation_report(
    summary: Mapping[str, Any],
    issues: list[PitValidationIssue],
) -> str:
    """Render a PIT validation Markdown report."""

    result_text = "PASS" if summary.get("passed") else "FAIL"
    lines = [
        "# PIT 股票池校验报告",
        "",
        "## 总览",
        "",
        "| 项目 | 值 |",
        "|---|---:|",
        f"| 结果 | {result_text} |",
        f"| 股票池数量 | {len(summary.get('actual_universes', []))} |",
        f"| 错误数 | {summary.get('error_count', 0)} |",
        f"| 警告数 | {summary.get('warning_count', 0)} |",
        f"| 最小成分股阈值 | {summary.get('min_symbols') or '未设置'} |",
        f"| 覆盖开始 | {summary.get('coverage_start') or '未设置'} |",
        f"| 覆盖结束 | {summary.get('coverage_end') or '未设置'} |",
        "",
        "## 股票池摘要",
        "",
        "| 股票池 | 行数 | 股票数 | 开始日期 | 结束日期 | 开放区间行数 |",
        "|---|---:|---:|---|---|---:|",
    ]
    universes = summary.get("diagnostics", {}).get("universes", {})
    for universe_id, item in universes.items():
        lines.append(
            "| "
            + " | ".join(
                [
                    str(universe_id),
                    str(item.get("rows", 0)),
                    str(item.get("symbols", 0)),
                    str(item.get("start_date") or ""),
                    str(item.get("end_date") or ""),
                    str(item.get("open_ended_rows", 0)),
                ]
            )
            + " |"
        )

    lines.extend(["", "## 问题列表", ""])
    if not issues:
        lines.append("无问题。")
        return "\n".join(lines).rstrip() + "\n"

    lines.extend(
        [
            "| 严重级别 | 代码 | 股票池 | 股票 | 日期 | 说明 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for issue in issues:
        lines.append(
            "| "
            + " | ".join(
                [
                    issue.severity,
                    issue.code,
                    issue.universe_id or "",
                    issue.symbol or "",
                    issue.date or "",
                    issue.message,
                ]
            )
            + " |"
        )
    return "\n".join(lines).rstrip() + "\n"


def pit_membership_diagnostics(memberships: pd.DataFrame) -> dict[str, Any]:
    """Return compact diagnostics for a normalized PIT membership frame."""

    data = normalize_pit_memberships(memberships)
    by_universe: dict[str, dict[str, Any]] = {}
    for universe_id, group in data.groupby("universe_id", sort=True):
        by_universe[str(universe_id)] = {
            "rows": int(len(group)),
            "symbols": int(group["symbol"].nunique()),
            "start_date": _date_str(group["start_date"].min()),
            "end_date": _date_str(group["end_date"].dropna().max()),
            "open_ended_rows": int(group["end_date"].isna().sum()),
        }

    return {
        "row_count": int(len(data)),
        "universe_count": int(data["universe_id"].nunique()),
        "symbol_count": int(data["symbol"].nunique()),
        "universes": by_universe,
    }


def filter_prices_by_pit_universe(
    prices: pd.DataFrame,
    provider: PointInTimeUniverseProvider,
    universe_id: str,
    *,
    date_col: str = "date",
    symbol_col: str = "symbol",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Filter long-form prices to point-in-time members for each trade date."""

    if prices.empty:
        return prices.copy(), {
            "universe_id": _normalize_universe_id(universe_id),
            "input_rows": 0,
            "output_rows": 0,
            "dates": 0,
            "empty_dates": [],
            "min_members": 0,
            "max_members": 0,
        }

    missing = [column for column in (date_col, symbol_col) if column not in prices.columns]
    if missing:
        raise ValueError(f"prices missing required columns: {', '.join(missing)}")

    frame = prices.copy()
    frame["_pit_date"] = pd.to_datetime(frame[date_col]).dt.date
    frame["_pit_symbol"] = frame[symbol_col].astype(str).str.upper()

    masks: list[pd.Series] = []
    member_counts: list[int] = []
    empty_dates: list[str] = []
    for trade_date, group in frame.groupby("_pit_date", sort=True):
        members = set(provider.get_symbols(universe_id, trade_date))
        member_counts.append(len(members))
        if not members:
            empty_dates.append(trade_date.isoformat())
        masks.append(group["_pit_symbol"].isin(members))

    keep = pd.concat(masks).sort_index() if masks else pd.Series(False, index=frame.index)
    filtered = frame.loc[keep].drop(columns=["_pit_date", "_pit_symbol"]).reset_index(drop=True)
    diagnostics = {
        "universe_id": _normalize_universe_id(universe_id),
        "input_rows": int(len(prices)),
        "output_rows": int(len(filtered)),
        "dates": int(frame["_pit_date"].nunique()),
        "empty_dates": empty_dates,
        "min_members": int(min(member_counts) if member_counts else 0),
        "max_members": int(max(member_counts) if member_counts else 0),
    }
    return filtered, diagnostics


def _normalize_universe_id(universe_id: str) -> str:
    return universe_id.removeprefix("preset_").lower()


def _check_symbol_format(data: pd.DataFrame, issues: list[PitValidationIssue]) -> None:
    bad = data[~data["symbol"].str.match(r"^\d{6}\.(SH|SZ|BJ)$", na=False)]
    for _, row in bad.iterrows():
        issues.append(
            PitValidationIssue(
                severity="error",
                code="invalid_symbol_format",
                message="股票代码应为 000001.SZ / 600000.SH / 430047.BJ 形式",
                universe_id=str(row["universe_id"]),
                symbol=str(row["symbol"]),
            )
        )


def _check_interval_overlaps(data: pd.DataFrame, issues: list[PitValidationIssue]) -> None:
    for (universe_id, symbol), group in data.groupby(["universe_id", "symbol"], sort=True):
        ordered = group.sort_values(["start_date", "end_date"])
        previous_end: pd.Timestamp | None = None
        previous_start: pd.Timestamp | None = None
        open_ended_seen = False
        for _, row in ordered.iterrows():
            start = pd.Timestamp(row["start_date"])
            end = row["end_date"] if pd.notna(row["end_date"]) else pd.NaT
            if open_ended_seen:
                issues.append(
                    PitValidationIssue(
                        severity="error",
                        code="interval_after_open_ended",
                        message="同一股票存在开放结束区间后续又开始的新区间",
                        universe_id=str(universe_id),
                        symbol=str(symbol),
                        date=start.date().isoformat(),
                    )
                )
            if previous_end is not None and start <= previous_end:
                issues.append(
                    PitValidationIssue(
                        severity="error",
                        code="overlapping_interval",
                        message=(
                            "同一股票在同一股票池内存在重叠生效区间: "
                            f"{previous_start.date().isoformat() if previous_start is not None else ''}"
                            f" - {previous_end.date().isoformat()} 与 {start.date().isoformat()}"
                        ),
                        universe_id=str(universe_id),
                        symbol=str(symbol),
                        date=start.date().isoformat(),
                    )
                )
            if pd.isna(end):
                open_ended_seen = True
                previous_end = None
            else:
                previous_end = pd.Timestamp(end)
                previous_start = start


def _check_min_symbols(
    data: pd.DataFrame,
    min_symbols: int | Mapping[str, int],
    issues: list[PitValidationIssue],
) -> None:
    for universe_id, group in data.groupby("universe_id", sort=True):
        threshold = _min_symbols_for(str(universe_id), min_symbols)
        if threshold is None:
            continue
        symbols = int(group["symbol"].nunique())
        if symbols < threshold:
            issues.append(
                PitValidationIssue(
                    severity="error",
                    code="too_few_symbols",
                    message=f"股票池总成分股数 {symbols} 小于阈值 {threshold}",
                    universe_id=str(universe_id),
                )
            )


def _check_coverage_dates(
    data: pd.DataFrame,
    coverage_dates: list[date],
    min_symbols: int | Mapping[str, int] | None,
    issues: list[PitValidationIssue],
) -> None:
    provider = PointInTimeUniverseProvider(data)
    for universe in provider.list_universes():
        threshold = _min_symbols_for(universe.id, min_symbols)
        for item in coverage_dates:
            symbols = provider.get_symbols(universe.id, item)
            if not symbols:
                issues.append(
                    PitValidationIssue(
                        severity="error",
                        code="empty_coverage_date",
                        message="股票池在指定覆盖日期没有有效成分股",
                        universe_id=universe.id,
                        date=item.isoformat(),
                    )
                )
            elif threshold is not None and len(symbols) < threshold:
                issues.append(
                    PitValidationIssue(
                        severity="error",
                        code="too_few_symbols_on_coverage_date",
                        message=f"股票池在指定覆盖日期有效成分股 {len(symbols)} 小于阈值 {threshold}",
                        universe_id=universe.id,
                        date=item.isoformat(),
                    )
                )


def _min_symbols_for(universe_id: str, min_symbols: int | Mapping[str, int] | None) -> int | None:
    if min_symbols is None:
        return None
    if isinstance(min_symbols, int):
        return min_symbols
    normalized = _normalize_universe_id(universe_id)
    if normalized in min_symbols:
        return int(min_symbols[normalized])
    upper = universe_id.upper()
    if upper in min_symbols:
        return int(min_symbols[upper])
    return None


def _coerce_date_or_none(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _date_str(value: Any) -> str | None:
    if pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value
