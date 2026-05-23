"""Static universe provider."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from cq.universe.base import Universe, UniverseNotFoundError


class StaticUniverseProvider:
    """Resolve static universe definitions."""

    def __init__(self, universes: Iterable[Universe | Mapping[str, object]]) -> None:
        self._universes: dict[str, Universe] = {}
        for raw in universes:
            universe = raw if isinstance(raw, Universe) else self._from_mapping(raw)
            if universe.id in self._universes:
                raise ValueError(f"duplicate universe id: {universe.id}")
            if not universe.symbols:
                raise ValueError(f"universe {universe.id} has no symbols")
            self._universes[universe.id] = universe

    def list_universes(self) -> list[Universe]:
        return list(self._universes.values())

    def get_universe(self, universe_id: str) -> Universe:
        normalized = _normalize_universe_id(universe_id)
        try:
            return self._universes[normalized]
        except KeyError as exc:
            raise UniverseNotFoundError(f"unknown universe: {universe_id}") from exc

    def get_symbols(self, universe_id: str, trade_date: date | None = None) -> list[str]:
        return list(self.get_universe(universe_id).symbols)

    @staticmethod
    def _from_mapping(raw: Mapping[str, object]) -> Universe:
        symbols_raw = raw.get("symbols")
        if not isinstance(symbols_raw, list | tuple):
            raise ValueError("universe symbols must be a list or tuple")
        symbols = tuple(_unique_symbols(str(symbol).upper() for symbol in symbols_raw))
        return Universe(
            id=str(raw["id"]),
            name=str(raw["name"]),
            source=str(raw.get("source", "builtin_preset")),
            construction=str(raw.get("construction", "static")),
            description=str(raw.get("description", "")),
            symbols=symbols,
            metadata=dict(raw.get("metadata", {}) or {}),
        )


def _normalize_universe_id(universe_id: str) -> str:
    return universe_id.removeprefix("preset_")


def _unique_symbols(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        if symbol and symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result
