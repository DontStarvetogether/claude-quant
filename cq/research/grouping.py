"""Factor grouping analysis."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FactorGroupAnalysis:
    """Container for factor grouping outputs."""

    group_return: pd.DataFrame
    group_nav: pd.DataFrame
    top_bottom_return: pd.DataFrame
    monotonicity: pd.DataFrame
    coverage: pd.DataFrame
    turnover_by_group: pd.DataFrame


def analyze_factor_groups(
    factor_df: pd.DataFrame,
    forward_return_df: pd.DataFrame | None = None,
    *,
    group_count: int = 5,
    periods: Sequence[int] = (1, 5, 20),
    date_col: str = "date",
    symbol_col: str = "symbol",
    factor_col: str = "factor",
) -> FactorGroupAnalysis:
    """Analyze cross-sectional factor groups by date.

    Higher factor values are assigned to higher group numbers. If
    ``forward_return_df`` is provided, it is merged with ``factor_df`` on
    ``date`` and ``symbol``; otherwise ``factor_df`` must already contain the
    forward return columns.
    """
    if group_count < 2:
        raise ValueError("group_count must be at least 2")
    normalized_periods = _normalize_periods(periods)

    data = _prepare_factor_return_data(
        factor_df,
        forward_return_df,
        date_col=date_col,
        symbol_col=symbol_col,
        factor_col=factor_col,
        periods=normalized_periods,
    )
    data = data.sort_values([date_col, symbol_col], kind="mergesort").reset_index(drop=True)
    data["group"] = _assign_groups(data, group_count, date_col=date_col, factor_col=factor_col)

    coverage = _coverage(data, date_col=date_col, factor_col=factor_col)
    group_return = _group_returns(data, normalized_periods, date_col=date_col)
    group_nav = _group_nav(group_return, date_col=date_col)
    top_bottom = _top_bottom(group_return, date_col=date_col)
    monotonicity = _monotonicity(group_return, date_col=date_col)
    turnover = _turnover_by_group(data, date_col=date_col, symbol_col=symbol_col)

    return FactorGroupAnalysis(
        group_return=group_return,
        group_nav=group_nav,
        top_bottom_return=top_bottom,
        monotonicity=monotonicity,
        coverage=coverage,
        turnover_by_group=turnover,
    )


def _prepare_factor_return_data(
    factor_df: pd.DataFrame,
    forward_return_df: pd.DataFrame | None,
    *,
    date_col: str,
    symbol_col: str,
    factor_col: str,
    periods: Sequence[int],
) -> pd.DataFrame:
    _require_columns(factor_df, [date_col, symbol_col, factor_col])
    return_cols = [f"forward_return_{period}d" for period in periods]

    factor = factor_df[[date_col, symbol_col, factor_col]].copy()
    if forward_return_df is None:
        _require_columns(factor_df, return_cols)
        returns = factor_df[[date_col, symbol_col, *return_cols]].copy()
    else:
        _require_columns(forward_return_df, [date_col, symbol_col, *return_cols])
        returns = forward_return_df[[date_col, symbol_col, *return_cols]].copy()

    data = factor.merge(returns, on=[date_col, symbol_col], how="left")
    data[factor_col] = pd.to_numeric(data[factor_col], errors="coerce")
    for col in return_cols:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    return data


def _assign_groups(
    data: pd.DataFrame,
    group_count: int,
    *,
    date_col: str,
    factor_col: str,
) -> pd.Series:
    def assign_one_date(group: pd.DataFrame) -> pd.Series:
        valid = group[factor_col].dropna()
        result = pd.Series(np.nan, index=group.index, dtype="float64")
        if len(valid) < 2:
            return result

        q = min(group_count, len(valid))
        ranks = valid.rank(method="first")
        labels = pd.qcut(ranks, q=q, labels=False, duplicates="drop")
        result.loc[valid.index] = labels.astype(float) + 1
        return result

    assigned = data.groupby(date_col, group_keys=False, sort=True).apply(assign_one_date)
    return assigned.reindex(data.index)


def _coverage(data: pd.DataFrame, *, date_col: str, factor_col: str) -> pd.DataFrame:
    rows = []
    for trade_date, group in data.groupby(date_col, sort=True):
        total = len(group)
        valid = int(group[factor_col].notna().sum())
        rows.append(
            {
                date_col: trade_date,
                "total_count": int(total),
                "available_count": valid,
                "coverage": valid / total if total else np.nan,
            }
        )
    return pd.DataFrame(rows, columns=[date_col, "total_count", "available_count", "coverage"])


def _group_returns(data: pd.DataFrame, periods: Sequence[int], *, date_col: str) -> pd.DataFrame:
    valid = data.dropna(subset=["group"]).copy()
    valid["group"] = valid["group"].astype(int)
    rows: list[dict] = []
    for period in periods:
        return_col = f"forward_return_{period}d"
        grouped = (
            valid.dropna(subset=[return_col])
            .groupby([date_col, "group"], sort=True)[return_col]
            .agg(["mean", "count"])
            .reset_index()
        )
        for _, row in grouped.iterrows():
            rows.append(
                {
                    date_col: row[date_col],
                    "period": int(period),
                    "group": int(row["group"]),
                    "mean_return": float(row["mean"]),
                    "count": int(row["count"]),
                }
            )
    return pd.DataFrame(rows, columns=[date_col, "period", "group", "mean_return", "count"])


def _group_nav(group_return: pd.DataFrame, *, date_col: str) -> pd.DataFrame:
    if group_return.empty:
        return pd.DataFrame(columns=[date_col, "period", "group", "nav"])

    rows: list[dict] = []
    for (period, group_id), group in group_return.groupby(["period", "group"], sort=True):
        nav = 1.0
        for _, row in group.sort_values(date_col).iterrows():
            nav *= 1.0 + float(row["mean_return"])
            rows.append(
                {
                    date_col: row[date_col],
                    "period": int(period),
                    "group": int(group_id),
                    "nav": float(nav),
                }
            )
    return pd.DataFrame(rows, columns=[date_col, "period", "group", "nav"])


def _top_bottom(group_return: pd.DataFrame, *, date_col: str) -> pd.DataFrame:
    rows: list[dict] = []
    for (trade_date, period), group in group_return.groupby([date_col, "period"], sort=True):
        if group["group"].nunique() < 2:
            top_bottom = np.nan
        else:
            bottom = group.loc[group["group"].idxmin(), "mean_return"]
            top = group.loc[group["group"].idxmax(), "mean_return"]
            top_bottom = float(top - bottom)
        rows.append({date_col: trade_date, "period": int(period), "top_bottom_return": top_bottom})
    return pd.DataFrame(rows, columns=[date_col, "period", "top_bottom_return"])


def _monotonicity(group_return: pd.DataFrame, *, date_col: str) -> pd.DataFrame:
    rows: list[dict] = []
    for period, period_group in group_return.groupby("period", sort=True):
        correlations: list[float] = []
        for _, date_group in period_group.groupby(date_col, sort=True):
            if date_group["group"].nunique() < 2 or date_group["mean_return"].nunique() < 2:
                continue
            corr = _spearman_corr(date_group["group"], date_group["mean_return"])
            if pd.notna(corr) and np.isfinite(corr):
                correlations.append(float(corr))
        rows.append(
            {
                "period": int(period),
                "mean_group_rank_corr": float(np.mean(correlations)) if correlations else np.nan,
                "monotonic_ratio": (
                    float(np.mean([corr > 0 for corr in correlations])) if correlations else np.nan
                ),
                "count": int(len(correlations)),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["period", "mean_group_rank_corr", "monotonic_ratio", "count"],
    )


def _turnover_by_group(
    data: pd.DataFrame,
    *,
    date_col: str,
    symbol_col: str,
) -> pd.DataFrame:
    valid = data.dropna(subset=["group"]).copy()
    if valid.empty:
        return pd.DataFrame(columns=[date_col, "group", "turnover"])
    valid["group"] = valid["group"].astype(int)

    rows: list[dict] = []
    previous: dict[int, set[str]] = {}
    for trade_date, date_group in valid.groupby(date_col, sort=True):
        current = {
            int(group_id): set(group[symbol_col].astype(str))
            for group_id, group in date_group.groupby("group", sort=True)
        }
        for group_id, members in current.items():
            prev_members = previous.get(group_id)
            if not prev_members:
                turnover = np.nan
            else:
                turnover = 1.0 - len(members & prev_members) / len(members) if members else np.nan
            rows.append({date_col: trade_date, "group": group_id, "turnover": turnover})
        previous = current
    return pd.DataFrame(rows, columns=[date_col, "group", "turnover"])


def _require_columns(df: pd.DataFrame, columns: Sequence[str]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")


def _normalize_periods(periods: Sequence[int]) -> tuple[int, ...]:
    if not periods:
        raise ValueError("periods must not be empty")
    normalized: list[int] = []
    for period in periods:
        if int(period) != period or int(period) <= 0:
            raise ValueError("periods must contain positive integers")
        p = int(period)
        if p not in normalized:
            normalized.append(p)
    return tuple(normalized)


def _spearman_corr(left: pd.Series, right: pd.Series) -> float:
    left_rank = left.rank(method="average")
    right_rank = right.rank(method="average")
    value = left_rank.corr(right_rank, method="pearson")
    return float(value) if pd.notna(value) and np.isfinite(value) else np.nan
