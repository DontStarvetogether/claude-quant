"""Point-in-time universe provider."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from cq.universe.base import Universe, UniverseNotFoundError


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
        self._universe_id_col = universe_id_col
        self._symbol_col = symbol_col
        self._start_date_col = start_date_col
        self._end_date_col = end_date_col
        self._name_col = name_col
        self._memberships = self._normalize_memberships(
            pd.DataFrame(memberships),
            required=[universe_id_col, symbol_col, start_date_col],
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

    def _normalize_memberships(self, df: pd.DataFrame, *, required: list[str]) -> pd.DataFrame:
        missing = [column for column in required if column not in df.columns]
        if missing:
            raise ValueError(f"memberships missing required columns: {', '.join(missing)}")
        data = df.copy()
        if self._end_date_col not in data.columns:
            data[self._end_date_col] = pd.NaT
        data[self._universe_id_col] = data[self._universe_id_col].astype(str).map(_normalize_universe_id)
        data[self._symbol_col] = data[self._symbol_col].astype(str).str.upper()
        data[self._start_date_col] = pd.to_datetime(data[self._start_date_col]).dt.normalize()
        data[self._end_date_col] = pd.to_datetime(data[self._end_date_col], errors="coerce").dt.normalize()
        data = data.drop_duplicates(
            [self._universe_id_col, self._symbol_col, self._start_date_col, self._end_date_col]
        )
        return data.sort_values([self._universe_id_col, self._start_date_col, self._symbol_col]).reset_index(drop=True)


def _normalize_universe_id(universe_id: str) -> str:
    return universe_id.removeprefix("preset_").lower()


def _date_str(value: Any) -> str | None:
    if pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()
