"""Runtime and health-check API."""

from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path

from fastapi import APIRouter

from cq import __version__
from cq.engine.backtest_engine import ENGINE_VERSION
from cq.utils.config import Config

router = APIRouter(prefix="/api", tags=["runtime"])


@router.get("/version")
async def version() -> dict[str, str]:
    """Return application and engine version metadata."""

    return {
        "app": "Claude Quant",
        "version": __version__,
        "engine_version": ENGINE_VERSION,
    }


@router.get("/runtime")
async def runtime() -> dict:
    """Return runtime capability information for the Web workbench."""

    config = Config.from_yaml("config/default.yaml")
    return {
        "app": "Claude Quant",
        "version": __version__,
        "engine_version": ENGINE_VERSION,
        "data_update_enabled": os.getenv("CQ_DISABLE_DATA_UPDATE") != "1",
        "storage_dir": str(Path(config.data.root_path).resolve()),
        "available_modules": {
            "backtest": _module_available("cq.engine"),
            "research": _module_available("cq.research"),
            "benchmark": _module_available("cq.benchmark"),
            "live": _module_available("cq.live"),
        },
    }


def _module_available(name: str) -> bool:
    return find_spec(name) is not None
