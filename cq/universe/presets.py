"""Built-in static universe presets."""

from __future__ import annotations

from cq.universe.base import Universe
from cq.universe.static import StaticUniverseProvider

BUILTIN_UNIVERSES: tuple[Universe, ...] = (
    Universe(
        id="core50",
        name="宽基核心50",
        source="builtin_preset",
        construction="static",
        description="核心宽基大盘蓝筹静态样本，用于快速验证。",
        symbols=(
            "600519.SH", "000858.SZ", "600036.SH", "601318.SH", "600276.SH",
            "000333.SZ", "002594.SZ", "300750.SZ", "601166.SH", "600887.SH",
            "601888.SH", "600030.SH", "000001.SZ", "600309.SH", "000651.SZ",
            "300760.SZ", "601398.SH", "601288.SH", "601988.SH", "601328.SH",
            "600000.SH", "600900.SH", "601012.SH", "600031.SH", "601899.SH",
            "601668.SH", "600016.SH", "601601.SH", "000002.SZ", "600048.SH",
            "600690.SH", "002415.SZ", "300015.SZ", "600438.SH", "600436.SH",
            "000725.SZ", "601633.SH", "600104.SH", "601857.SH", "600028.SH",
            "601088.SH", "601766.SH", "600019.SH", "600585.SH", "000063.SZ",
            "002475.SZ", "600406.SH", "603259.SH", "300059.SZ", "000776.SZ",
        ),
    ),
    Universe(
        id="bluechip",
        name="蓝筹稳健",
        source="builtin_preset",
        construction="static",
        description="大盘蓝筹静态样本。",
        symbols=(
            "600519.SH", "000858.SZ", "600036.SH", "601318.SH", "601166.SH",
            "600276.SH", "000333.SZ", "600887.SH", "600309.SH", "601398.SH",
            "601288.SH", "601988.SH", "601328.SH", "600900.SH", "600000.SH",
            "601088.SH", "600028.SH", "601857.SH", "600019.SH", "600585.SH",
            "600104.SH", "600690.SH", "601601.SH", "600048.SH", "000002.SZ",
            "601668.SH", "601766.SH", "600031.SH", "601899.SH", "600030.SH",
        ),
    ),
    Universe(
        id="finance",
        name="金融地产",
        source="builtin_preset",
        construction="static",
        description="金融地产静态样本。",
        symbols=(
            "600036.SH", "601318.SH", "601166.SH", "000001.SZ", "600000.SH",
            "601398.SH", "601288.SH", "601988.SH", "601328.SH", "600016.SH",
            "601601.SH", "601628.SH", "601336.SH", "600030.SH", "600837.SH",
            "000776.SZ", "600958.SH", "601688.SH", "601788.SH", "000002.SZ",
            "600048.SH", "001979.SZ", "600383.SH", "000069.SZ", "601155.SH",
        ),
    ),
    Universe(
        id="consume_health",
        name="消费医药",
        source="builtin_preset",
        construction="static",
        description="消费和医药静态样本。",
        symbols=(
            "600519.SH", "000858.SZ", "600887.SH", "601888.SH", "000333.SZ",
            "000651.SZ", "600690.SH", "000568.SZ", "000596.SZ", "603288.SH",
            "600809.SH", "600298.SH", "600872.SH", "300498.SZ", "600276.SH",
            "300760.SZ", "600436.SH", "603259.SH", "000661.SZ", "300015.SZ",
            "002821.SZ", "600085.SH", "000538.SZ", "600196.SH", "300122.SZ",
        ),
    ),
    Universe(
        id="growth",
        name="科技新能源",
        source="builtin_preset",
        construction="static",
        description="科技和新能源静态样本。",
        symbols=(
            "300750.SZ", "002594.SZ", "601012.SH", "600438.SH", "300014.SZ",
            "002812.SZ", "002475.SZ", "300274.SZ", "002129.SZ", "300124.SZ",
            "600406.SH", "000725.SZ", "002371.SZ", "000063.SZ", "002415.SZ",
            "603501.SH", "688981.SH", "688111.SH", "688012.SH", "300059.SZ",
            "300760.SZ", "300015.SZ", "300122.SZ", "002230.SZ", "002236.SZ",
        ),
    ),
)

_BUILTIN_PROVIDER = StaticUniverseProvider(BUILTIN_UNIVERSES)


def get_builtin_universe_provider() -> StaticUniverseProvider:
    return _BUILTIN_PROVIDER


def get_builtin_universe_presets() -> list[dict[str, object]]:
    return [universe.to_dict() for universe in _BUILTIN_PROVIDER.list_universes()]
