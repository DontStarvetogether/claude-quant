"""Forward return labels for single-factor research."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def calculate_forward_returns(
    price_df: pd.DataFrame,
    periods: Sequence[int] = (1, 5, 20),
    *,
    date_col: str = "date",
    symbol_col: str = "symbol",
    price_col: str = "close",
) -> pd.DataFrame:
    """Calculate future returns per symbol without crossing symbol boundaries.

    Input is a long table with at least ``date``, ``symbol`` and ``close``.
    Output keeps one row per input row and appends ``forward_return_{n}d``
    columns, where each value is ``close[t+n] / close[t] - 1`` for the same
    symbol.
    """
    _require_columns(price_df, [date_col, symbol_col, price_col])
    normalized_periods = _normalize_periods(periods)

    df = price_df[[date_col, symbol_col, price_col]].copy()
    df = df.sort_values([symbol_col, date_col], kind="mergesort").reset_index(drop=True)
    prices = pd.to_numeric(df[price_col], errors="coerce")
    grouped_prices = prices.groupby(df[symbol_col], sort=False)

    out = df[[date_col, symbol_col]].copy()
    for period in normalized_periods:
        future = grouped_prices.shift(-period)
        returns = future / prices - 1.0
        returns = returns.where(np.isfinite(returns), np.nan)
        out[f"forward_return_{period}d"] = returns

    return out


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
