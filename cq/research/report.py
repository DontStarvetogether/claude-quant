"""Markdown report generation for factor research."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from cq.research.grouping import FactorGroupAnalysis

SCHEMA_VERSION = "factor_report.v1"


@dataclass(frozen=True)
class FactorReport:
    """Rendered factor report."""

    markdown: str


@dataclass(frozen=True)
class FactorReportExport:
    """Paths written by factor report export."""

    output_dir: Path
    files: dict[str, Path]
    report: FactorReport


def generate_factor_report(
    *,
    factor_name: str,
    analysis: FactorGroupAnalysis,
    ic_summary: pd.DataFrame,
    universe: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    sample_split_date: str | None = None,
    date_col: str = "date",
) -> FactorReport:
    """Generate a Markdown summary for single-factor analysis results."""
    _validate_analysis(analysis, ic_summary, date_col=date_col)

    lines: list[str] = [f"# 因子报告：{factor_name}", ""]
    lines.extend(_scope_section(universe, start_date, end_date, metadata))
    lines.extend(_coverage_section(analysis.coverage))
    lines.extend(_sample_split_section(analysis.coverage, sample_split_date, date_col=date_col))
    lines.extend(_ic_section(ic_summary))
    lines.extend(_group_section(analysis.group_return, analysis.group_nav, date_col=date_col))
    lines.extend(_top_bottom_section(analysis.top_bottom_return))
    lines.extend(_monotonicity_section(analysis.monotonicity))
    lines.extend(_turnover_section(analysis.turnover_by_group))

    return FactorReport(markdown="\n".join(lines).rstrip() + "\n")


def export_factor_report(
    *,
    factor_name: str,
    analysis: FactorGroupAnalysis,
    ic_summary: pd.DataFrame,
    output_dir: str | Path,
    universe: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    sample_split_date: str | None = None,
    date_col: str = "date",
    include_markdown: bool = True,
) -> FactorReportExport:
    """Export factor research tables, summary JSON, and optional Markdown."""
    report = generate_factor_report(
        factor_name=factor_name,
        analysis=analysis,
        ic_summary=ic_summary,
        universe=universe,
        start_date=start_date,
        end_date=end_date,
        metadata=metadata,
        sample_split_date=sample_split_date,
        date_col=date_col,
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    tables = _analysis_tables(analysis, ic_summary)
    for key, frame in tables.items():
        path = out / f"{key}.csv"
        _frame_for_export(frame).to_csv(path, index=False)
        files[key] = path

    summary_payload = {
        "schema_version": SCHEMA_VERSION,
        "factor_name": factor_name,
        "universe": universe,
        "start_date": start_date,
        "end_date": end_date,
        "metadata": dict(metadata or {}),
        "sample_split_date": sample_split_date,
        "sample_diagnostics": sample_split_diagnostics(
            analysis.coverage,
            sample_split_date,
            date_col=date_col,
        ),
        "table_rows": {key: int(len(frame)) for key, frame in tables.items()},
        "ic_summary": _json_records(ic_summary),
        "files": {key: path.name for key, path in files.items()},
    }
    summary_path = out / "summary.json"
    summary_path.write_text(
        json.dumps(_json_safe(summary_payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files["summary"] = summary_path

    if include_markdown:
        report_path = out / "report.md"
        report_path.write_text(report.markdown, encoding="utf-8")
        files["report"] = report_path

    return FactorReportExport(output_dir=out, files=files, report=report)


def _scope_section(
    universe: str | None,
    start_date: str | None,
    end_date: str | None,
    metadata: Mapping[str, Any] | None,
) -> list[str]:
    rows = [
        ["股票池", universe or "—"],
        ["测试区间", _date_range(start_date, end_date)],
    ]
    if metadata:
        rows.extend([[str(k), str(v)] for k, v in metadata.items()])
    return ["## 测试范围", "", _markdown_table(["项目", "值"], rows), ""]


def _coverage_section(coverage: pd.DataFrame) -> list[str]:
    if coverage.empty:
        rows = [["样本日期数", "0"], ["平均覆盖率", "—"], ["最低覆盖率", "—"], ["平均股票数", "—"]]
    else:
        rows = [
            ["样本日期数", str(len(coverage))],
            ["平均覆盖率", _fmt_pct(coverage["coverage"].mean())],
            ["最低覆盖率", _fmt_pct(coverage["coverage"].min())],
            ["平均股票数", _fmt_num(coverage["available_count"].mean(), 1)],
        ]
    return ["## 覆盖率", "", _markdown_table(["指标", "值"], rows), ""]


def sample_split_diagnostics(
    coverage: pd.DataFrame,
    sample_split_date: str | None,
    *,
    date_col: str = "date",
) -> dict[str, Any]:
    """Summarize in-sample/out-of-sample coverage around a split date."""

    if not sample_split_date:
        return {"status": "unavailable", "reason": "sample_split_date not provided"}
    if coverage.empty:
        return {"status": "unavailable", "reason": "coverage is empty"}
    if date_col not in coverage.columns:
        raise ValueError(f"coverage missing date column: {date_col}")

    split = pd.Timestamp(sample_split_date)
    data = coverage.copy()
    data[date_col] = pd.to_datetime(data[date_col])
    in_sample = data[data[date_col] <= split]
    out_sample = data[data[date_col] > split]
    return {
        "status": "available",
        "split_date": split.date().isoformat(),
        "in_sample_dates": int(len(in_sample)),
        "out_of_sample_dates": int(len(out_sample)),
        "in_sample_avg_coverage": _safe_mean(in_sample, "coverage"),
        "out_of_sample_avg_coverage": _safe_mean(out_sample, "coverage"),
    }


def _sample_split_section(
    coverage: pd.DataFrame,
    sample_split_date: str | None,
    *,
    date_col: str,
) -> list[str]:
    diagnostics = sample_split_diagnostics(coverage, sample_split_date, date_col=date_col)
    if diagnostics["status"] != "available":
        return []
    rows = [
        ["切分日期", str(diagnostics["split_date"])],
        ["样本内日期数", str(diagnostics["in_sample_dates"])],
        ["样本外日期数", str(diagnostics["out_of_sample_dates"])],
        ["样本内平均覆盖率", _fmt_pct(diagnostics["in_sample_avg_coverage"])],
        ["样本外平均覆盖率", _fmt_pct(diagnostics["out_of_sample_avg_coverage"])],
    ]
    return ["## 样本切分诊断", "", _markdown_table(["项目", "值"], rows), ""]


def _ic_section(ic_summary: pd.DataFrame) -> list[str]:
    rows = []
    for _, row in ic_summary.sort_values("period").iterrows():
        rows.append(
            [
                f"{int(row['period'])}D",
                _fmt_num(row["ic_mean"], 4),
                _fmt_num(row["ic_std"], 4),
                _fmt_num(row["icir"], 3),
                _fmt_pct(row["ic_win_rate"]),
                str(int(row["count"])),
            ]
        )
    return [
        "## IC 分析",
        "",
        _markdown_table(["周期", "IC Mean", "IC Std", "ICIR", "IC 胜率", "样本数"], rows),
        "",
    ]


def _group_section(group_return: pd.DataFrame, group_nav: pd.DataFrame, *, date_col: str) -> list[str]:
    rows = []
    if not group_return.empty:
        for (period, group_id), group in group_return.groupby(["period", "group"], sort=True):
            final_nav = _final_nav(group_nav, int(period), int(group_id), date_col=date_col)
            rows.append(
                [
                    f"{int(period)}D",
                    str(int(group_id)),
                    _fmt_pct(group["mean_return"].mean()),
                    _fmt_num(group["count"].mean(), 1),
                    _fmt_num(final_nav, 4),
                ]
            )
    return [
        "## 分层收益",
        "",
        _markdown_table(["周期", "分组", "平均未来收益", "平均样本数", "期末净值"], rows),
        "",
    ]


def _top_bottom_section(top_bottom: pd.DataFrame) -> list[str]:
    rows = []
    if not top_bottom.empty:
        for period, group in top_bottom.groupby("period", sort=True):
            series = pd.to_numeric(group["top_bottom_return"], errors="coerce").dropna()
            rows.append(
                [
                    f"{int(period)}D",
                    _fmt_pct(series.mean() if len(series) else np.nan),
                    _fmt_pct(_compound_return(series)),
                    _fmt_pct((series > 0).mean() if len(series) else np.nan),
                    str(int(len(series))),
                ]
            )
    return [
        "## Top-Bottom",
        "",
        _markdown_table(["周期", "平均收益", "复合收益", "胜率", "样本数"], rows),
        "",
    ]


def _monotonicity_section(monotonicity: pd.DataFrame) -> list[str]:
    rows = []
    if not monotonicity.empty:
        for _, row in monotonicity.sort_values("period").iterrows():
            rows.append(
                [
                    f"{int(row['period'])}D",
                    _fmt_num(row["mean_group_rank_corr"], 4),
                    _fmt_pct(row["monotonic_ratio"]),
                    str(int(row["count"])),
                ]
            )
    return [
        "## 单调性",
        "",
        _markdown_table(["周期", "平均组序相关", "正单调比例", "样本数"], rows),
        "",
    ]


def _turnover_section(turnover: pd.DataFrame) -> list[str]:
    rows = []
    if not turnover.empty:
        for group_id, group in turnover.groupby("group", sort=True):
            series = pd.to_numeric(group["turnover"], errors="coerce").dropna()
            rows.append([str(int(group_id)), _fmt_pct(series.mean() if len(series) else np.nan)])
    return ["## 分组换手", "", _markdown_table(["分组", "平均换手率"], rows), ""]


def _final_nav(group_nav: pd.DataFrame, period: int, group_id: int, *, date_col: str) -> float:
    if group_nav.empty:
        return np.nan
    selected = group_nav[(group_nav["period"] == period) & (group_nav["group"] == group_id)]
    if selected.empty:
        return np.nan
    return float(selected.sort_values(date_col)["nav"].iloc[-1])


def _compound_return(series: pd.Series) -> float:
    if series.empty:
        return np.nan
    return float((1.0 + series).prod() - 1.0)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        rows = [["—" for _ in headers]]
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(_escape_cell(str(cell)) for cell in row) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _fmt_pct(value: Any, digits: int = 2) -> str:
    number = _to_float(value)
    if number is None:
        return "—"
    return f"{number * 100:.{digits}f}%"


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _to_float(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}"


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _safe_mean(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame.columns:
        return None
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return None
    return float(series.mean())


def _date_range(start_date: str | None, end_date: str | None) -> str:
    if start_date and end_date:
        return f"{start_date} 至 {end_date}"
    if start_date:
        return f"{start_date} 起"
    if end_date:
        return f"截至 {end_date}"
    return "—"


def _validate_analysis(
    analysis: FactorGroupAnalysis,
    ic_summary: pd.DataFrame,
    *,
    date_col: str,
) -> None:
    _require_columns(analysis.coverage, [date_col, "available_count", "coverage"])
    _require_columns(ic_summary, ["period", "ic_mean", "ic_std", "icir", "ic_win_rate", "count"])
    _require_columns(analysis.group_return, [date_col, "period", "group", "mean_return", "count"])
    _require_columns(analysis.group_nav, [date_col, "period", "group", "nav"])
    _require_columns(analysis.top_bottom_return, [date_col, "period", "top_bottom_return"])
    _require_columns(analysis.monotonicity, ["period", "mean_group_rank_corr", "monotonic_ratio", "count"])
    _require_columns(analysis.turnover_by_group, [date_col, "group", "turnover"])


def _require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")


def _analysis_tables(
    analysis: FactorGroupAnalysis,
    ic_summary: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {
        "coverage": analysis.coverage,
        "ic_summary": ic_summary,
        "group_return": analysis.group_return,
        "group_nav": analysis.group_nav,
        "top_bottom_return": analysis.top_bottom_return,
        "monotonicity": analysis.monotonicity,
        "turnover_by_group": analysis.turnover_by_group,
    }


def _frame_for_export(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.copy().reset_index(drop=True)


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [_json_safe(row) for row in frame.to_dict("records")]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value
