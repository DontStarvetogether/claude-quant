"""股票池相关 API。"""

from fastapi import APIRouter

from web.schemas import UniverseInfo, UniversesResponse
from web.universe_registry import get_universe_presets

router = APIRouter(prefix="/api/universes", tags=["universe"])


@router.get("", response_model=UniversesResponse)
async def list_universes() -> UniversesResponse:
    """列出内置股票池预设。"""
    return UniversesResponse(
        universes=[UniverseInfo(**item) for item in get_universe_presets()]
    )
