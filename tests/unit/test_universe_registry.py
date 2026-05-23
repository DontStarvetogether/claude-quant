"""单元测试：内置股票池 registry。"""

from web.universe_registry import get_universe_presets


def test_universe_presets_have_required_metadata():
    presets = get_universe_presets()

    assert presets
    ids = [item["id"] for item in presets]
    assert len(ids) == len(set(ids))
    for item in presets:
        assert item["name"]
        assert item["source"] == "builtin_preset"
        assert item["construction"] == "static"
        assert item["symbols"]
        assert "metadata" in item


def test_universe_presets_return_copied_symbol_lists():
    first = get_universe_presets()
    first[0]["symbols"].append("999999.SH")

    second = get_universe_presets()
    assert "999999.SH" not in second[0]["symbols"]
