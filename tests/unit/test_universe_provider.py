from __future__ import annotations

import pytest

from cq.universe import (
    StaticUniverseProvider,
    Universe,
    UniverseNotFoundError,
    get_builtin_universe_presets,
    get_builtin_universe_provider,
)


def test_builtin_provider_lists_static_universes():
    provider = get_builtin_universe_provider()
    universes = provider.list_universes()

    assert universes
    assert {u.id for u in universes} >= {"core50", "bluechip", "growth"}
    assert all(u.source == "builtin_preset" for u in universes)
    assert all(u.construction == "static" for u in universes)


def test_builtin_provider_resolves_symbols_and_preset_alias():
    provider = get_builtin_universe_provider()

    plain = provider.get_symbols("bluechip")
    alias = provider.get_symbols("preset_bluechip")

    assert plain == alias
    assert "600519.SH" in plain
    plain.append("999999.SH")
    assert "999999.SH" not in provider.get_symbols("bluechip")


def test_builtin_provider_raises_for_unknown_universe():
    provider = get_builtin_universe_provider()

    with pytest.raises(UniverseNotFoundError):
        provider.get_symbols("missing")


def test_static_provider_deduplicates_symbols_and_validates_ids():
    provider = StaticUniverseProvider(
        [
            {
                "id": "custom",
                "name": "Custom",
                "symbols": ["000001.sz", "000001.SZ", "600000.SH"],
            }
        ]
    )

    assert provider.get_symbols("custom") == ["000001.SZ", "600000.SH"]

    with pytest.raises(ValueError, match="duplicate universe id"):
        StaticUniverseProvider(
            [
                Universe(id="dup", name="A", symbols=("000001.SZ",)),
                Universe(id="dup", name="B", symbols=("600000.SH",)),
            ]
        )


def test_builtin_universe_presets_are_json_ready_copies():
    first = get_builtin_universe_presets()
    first[0]["symbols"].append("999999.SH")

    second = get_builtin_universe_presets()
    assert "999999.SH" not in second[0]["symbols"]
    assert isinstance(second[0]["metadata"], dict)
