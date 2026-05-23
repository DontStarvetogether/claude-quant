"""Universe provider abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


class UniverseNotFoundError(KeyError):
    """Raised when a universe id cannot be resolved."""


@dataclass(frozen=True)
class Universe:
    """A stock universe definition.

    Static universes ignore ``trade_date``. Future PIT providers can keep this
    shape while resolving membership by date.
    """

    id: str
    name: str
    symbols: tuple[str, ...]
    source: str = "builtin_preset"
    construction: str = "static"
    description: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "construction": self.construction,
            "description": self.description,
            "symbols": list(self.symbols),
            "metadata": dict(self.metadata),
        }


class UniverseProvider(Protocol):
    """Common universe provider interface."""

    def list_universes(self) -> list[Universe]:
        """Return available universe definitions."""
        ...

    def get_universe(self, universe_id: str) -> Universe:
        """Return a universe definition by id."""
        ...

    def get_symbols(self, universe_id: str, trade_date: date | None = None) -> list[str]:
        """Return member symbols for a universe and optional trade date."""
        ...
