"""策略相关 API"""

from fastapi import APIRouter

from web.runner import get_strategy_list
from web.schemas import StrategiesResponse, StrategyInfo, StrategyParam

router = APIRouter(prefix="/api/strategies", tags=["strategy"])


@router.get("", response_model=StrategiesResponse)
async def list_strategies() -> StrategiesResponse:
    """列出所有可用策略及其参数定义。"""
    raw = get_strategy_list()
    strategies = []
    for s in raw:
        params = [StrategyParam(**p) for p in s.get("params", [])]
        strategies.append(StrategyInfo(
            id=s["id"],
            name=s["name"],
            description=s["description"],
            params=params,
        ))
    return StrategiesResponse(strategies=strategies)
