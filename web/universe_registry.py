"""Web compatibility wrapper for built-in universe presets."""

from __future__ import annotations

from cq.universe import get_builtin_universe_presets


def get_universe_presets() -> list[dict[str, object]]:
    return get_builtin_universe_presets()
