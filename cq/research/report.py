"""Markdown report generation for factor research."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from cq.research.grouping import FactorGroupAnalysis


@dataclass(frozen=True)
class FactorReport:
    """Rendered factor report."""

    markdown: str


def generate_factor_report(
    *,
    factor_name: str,
    analysis: FactorGroupAnalysis,
    ic_summary: pd.DataFrame,
    universe: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    date_col: str = "date",
) -> FactorReport:
    """Generate a Markdown summary for single-factor analysis results."""
    _validate_analysis(analysis, ic_summary, date_col=date_col)

    lines: list[str] = [f"# 因子报告：{factor_name}", ""]
    lines.extend(_scope_section(universe, start_date, end_date, metadata))
    lines.extend(_coverage_section(analysis.coverage))
    lines.extend(_ic_section(ic_summary))
    lines.extend(_group_section(analysis.group_return, analysis.group_nav, date_col=date_col))
    lines.extend(_top_bottom_section(analysis.top_bottom_return))
    lines.extend(_monotonicity_section(analysis.monotonicity))
    lines.extend(_turnover_section(analysis.turnover_by_group))

    return FactorReport(markdown="\n".join(lines).rstrip() + "\n")


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
