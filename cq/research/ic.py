"""Information coefficient analysis for factor research."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def calculate_ic(
    factor_return_df: pd.DataFrame,
    periods: Sequence[int] = (1, 5, 20),
    *,
    date_col: str = "date",
    factor_col: str = "factor",
    method: str = "spearman",
) -> pd.DataFrame:
    """Calculate per-date IC for each forward return period.

    ``spearman`` is implemented as Pearson correlation of ranks, so the core
    research module does not require SciPy. Dates with fewer than two valid
    samples or no cross-sectional variation return ``NaN`` IC instead of
    raising.
    """
    if method not in {"spearman", "pearson"}:
        raise ValueError("method must be one of: spearman, pearson")
    _require_columns(factor_return_df, [date_col, factor_col])
    normalized_periods = _normalize_periods(periods)

    rows: list[dict] = []
    for trade_date, group in factor_return_df.groupby(date_col, sort=True):
        factor = pd.to_numeric(group[factor_col], errors="coerce")
        for period in normalized_periods:
            return_col = f"forward_return_{period}d"
            _require_columns(group, [return_col])
            forward_return = pd.to_numeric(group[return_col], errors="coerce")
            valid = pd.DataFrame({"factor": factor, "return": forward_return}).dropna()
            ic = _corr_or_nan(valid["factor"], valid["return"], method)
            rows.append(
                {
                    date_col: trade_date,
                    "period": period,
                    "ic": ic,
                    "sample_count": int(len(valid)),
                }
            )

    return pd.DataFrame(rows, columns=[date_col, "period", "ic", "sample_count"])


def summarize_ic(ic_df: pd.DataFrame) -> pd.DataFrame:
    """Summarize IC series by period."""
    _require_columns(ic_df, ["period", "ic"])
    rows: list[dict] = []
    for period, group in ic_df.groupby("period", sort=True):
        ic = pd.to_numeric(group["ic"], errors="coerce").dropna()
        mean = float(ic.mean()) if len(ic) else np.nan
        std = float(ic.std(ddof=1)) if len(ic) > 1 else np.nan
        rows.append(
            {
                "period": int(period),
                "ic_mean": mean,
                "ic_std": std,
                "icir": mean / std if std and np.isfinite(std) else np.nan,
                "ic_win_rate": float((ic > 0).mean()) if len(ic) else np.nan,
                "count": int(len(ic)),
            }
        )
    return pd.DataFrame(rows, columns=["period", "ic_mean", "ic_std", "icir", "ic_win_rate", "count"])


def _corr_or_nan(left: pd.Series, right: pd.Series, method: str) -> float:
    if len(left) < 2 or left.nunique(dropna=True) < 2 or right.nunique(dropna=True) < 2:
        return np.nan
    if method == "spearman":
        left = left.rank(method="average")
        right = right.rank(method="average")
    value = left.corr(right, method="pearson")
    return float(value) if pd.notna(value) and np.isfinite(value) else np.nan


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
