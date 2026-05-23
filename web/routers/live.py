"""实盘/模拟盘相关 API"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from cq.strategy.registry import validate_strategy_params
from web.live_runner import live_store, start_paper_session, start_live_session, stop_session
from web import db
from web.schemas import (
    LiveSessionsResponse,
    LiveSessionStatus,
    LiveSessionSummary,
    LiveStartRequest,
    LiveStartResponse,
)

router = APIRouter(prefix="/api/live", tags=["live"])


@router.post("/start", response_model=LiveStartResponse, status_code=202)
async def start_live(req: LiveStartRequest) -> LiveStartResponse:
    """启动模拟盘或实盘会话。"""
    try:
        strategy_params = validate_strategy_params(req.strategy_id, req.strategy_params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    if req.mode == "live":
        # 实盘模式
        if not req.account_id:
            raise HTTPException(status_code=400, detail="实盘模式需要提供 account_id（QMT 资金账号）")
        session = start_live_session(
            strategy_id=req.strategy_id,
            symbols=req.symbols,
            initial_capital=req.initial_capital,
            strategy_params=strategy_params,
            risk_params=req.risk.model_dump(),
            account_id=req.account_id,
            mini_qmt_dir=req.mini_qmt_dir,
        )
    else:
        # 模拟盘模式
        if not req.start_date or not req.end_date:
            raise HTTPException(status_code=400, detail="模拟盘模式需要提供 start_date 和 end_date")
        session = start_paper_session(
            strategy_id=req.strategy_id,
            symbols=req.symbols,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.initial_capital,
            strategy_params=strategy_params,
            risk_params=req.risk.model_dump(),
        )
    return LiveStartResponse(session_id=session.session_id, status="starting")


@router.post("/{session_id}/stop")
async def stop_live(session_id: str) -> dict:
    """停止会话。"""
    if not stop_session(session_id):
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")
    return {"session_id": session_id, "status": "stopped"}


@router.delete("/{session_id}")
async def delete_live(session_id: str) -> dict:
    """删除会话及其所有关联数据。"""
    # 先停止运行中的会话
    s = live_store.get(session_id)
    if s is not None:
        if s.status in ("running", "starting"):
            stop_session(session_id)
        live_store.remove(session_id)
    if not db.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")
    return {"session_id": session_id, "status": "deleted"}


@router.get("/{session_id}/status", response_model=LiveSessionStatus)
async def get_status(session_id: str) -> LiveSessionStatus:
    """查询会话实时状态（内存优先，DB 兜底）。"""
    s = live_store.get(session_id)
    if s is not None:
        return _to_status(s)
    # 尝试从 DB 加载历史会话
    d = db.get_session(session_id)
    if d is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")

    # 从 DB 加载持仓快照
    positions: list[dict] = []
    if d.get("final_positions"):
        try:
            positions = json.loads(d["final_positions"])
        except (json.JSONDecodeError, TypeError):
            pass

    # 从 DB 加载成交记录（停止的会话返回全部，便于完整查看）
    trades = db.get_trades(session_id)

    # 从 DB 加载绩效指标
    payload = _load_metrics_from_db(session_id)
    metrics = payload.get("metrics") if payload else None
    equity_curve = payload.get("equity") if payload else None

    return LiveSessionStatus(
        session_id=d["session_id"],
        strategy_id=d["strategy_id"],
        symbols=d["symbols"],
        mode=d.get("mode", "paper"),
        status=d["status"],
        current_date=d.get("end_date"),
        total_assets=d.get("total_assets"),
        cash=d.get("cash"),
        initial_capital=d.get("initial_capital", 1_000_000),
        positions=positions,
        recent_trades=trades,
        elapsed_seconds=round(_get_elapsed(d, d), 1),
        metrics=metrics,
        equity_curve=equity_curve,
        error=d.get("error"),
        started_at=d["started_at"],
    )


@router.get("/{session_id}/stream")
async def stream_status(session_id: str) -> StreamingResponse:
    """SSE 实时推送会话状态。"""
    s = live_store.get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")

    async def event_generator() -> AsyncIterator[str]:
        last_date: str | None = None
        last_assets: float | None = None
        last_trade_count = 0

        while True:
            s = live_store.get(session_id)
            if s is None:
                break

            changed = (
                s.current_date != last_date
                or s.total_assets != last_assets
                or len(s.recent_trades) != last_trade_count
            )

            if s.status in ("running", "starting") and changed:
                last_date = s.current_date
                last_assets = s.total_assets
                last_trade_count = len(s.recent_trades)
                data = json.dumps(_to_status_dict(s), ensure_ascii=False)
                yield f"event: status\ndata: {data}\n\n"

            elif s.status == "stopped":
                data = json.dumps(_to_status_dict(s), ensure_ascii=False)
                yield f"event: stopped\ndata: {data}\n\n"
                break

            elif s.status == "failed":
                data = json.dumps({"error": s.error or "会话异常"}, ensure_ascii=False)
                yield f"event: error\ndata: {data}\n\n"
                break

            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions", response_model=LiveSessionsResponse)
async def list_sessions() -> LiveSessionsResponse:
    """列出所有会话（合并内存活跃会话 + DB 历史会话）。"""
    # 内存中的活跃会话（状态最新）
    active_ids: set[str] = set()
    sessions: list[LiveSessionSummary] = []
    for s in live_store.all():
        active_ids.add(s.session_id)
        sessions.append(LiveSessionSummary(
            session_id=s.session_id,
            strategy_id=s.strategy_id,
            symbols=s.symbols,
            mode=s.mode,
            status=s.status,
            total_assets=s.total_assets,
            elapsed_seconds=round(_get_elapsed(s), 1),
            started_at=s.started_at.isoformat(),
        ))
    # DB 中的历史会话（排除内存中已有的）
    for d in db.list_sessions():
        if d["session_id"] not in active_ids:
            sessions.append(LiveSessionSummary(
                session_id=d["session_id"],
                strategy_id=d["strategy_id"],
                symbols=d["symbols"],
                mode=d.get("mode", "paper"),
                status=d["status"],
                total_assets=d.get("total_assets"),
                elapsed_seconds=round(_get_elapsed(d, d), 1),  # s 和 d 都传 dict 以覆盖所有分支
                started_at=d["started_at"],
            ))
    return LiveSessionsResponse(sessions=sessions)


@router.get("/{session_id}/trades")
async def get_trades(session_id: str) -> list[dict]:
    """获取会话的所有成交记录。"""
    return db.get_trades(session_id)


@router.get("/{session_id}/equity")
async def get_equity(session_id: str) -> list[dict]:
    """获取会话的净值曲线数据。"""
    return db.get_equity_curve(session_id)


def _get_elapsed(s: object, d: dict | None = None) -> float:
    """获取耗时，优先取内存/DB 中的值，若无则从 started_at/finished_at 推算。"""
    # 内存对象
    if hasattr(s, 'elapsed_seconds') and s.elapsed_seconds > 0:
        return s.elapsed_seconds
    # DB 字典
    if d and d.get("elapsed_seconds", 0) > 0:
        return d["elapsed_seconds"]
    # 从时间戳推算
    started = getattr(s, 'started_at', None) if hasattr(s, 'started_at') else None
    finished = None
    if d:
        started = started or d.get("started_at")
        finished = d.get("finished_at")
    if started:
        from datetime import datetime
        try:
            st = datetime.fromisoformat(str(started))
            ed = datetime.fromisoformat(str(finished)) if finished else datetime.now()
            return max(0, (ed - st).total_seconds())
        except Exception:
            pass
    return 0.0


def _load_metrics_from_db(session_id: str) -> dict | None:
    """从 DB 加载会话的绩效指标和净值曲线数据。"""
    d = db.get_session(session_id)
    if not d or not d.get("metrics_json"):
        return None
    try:
        return json.loads(d["metrics_json"])
    except (json.JSONDecodeError, TypeError):
        return None


# ── 内部工具 ──────────────────────────────────────────────────────────────────


def _to_status(s) -> LiveSessionStatus:
    return LiveSessionStatus(**_to_status_dict(s))


def _to_status_dict(s) -> dict:
    # 停止的会话尝试从 DB 加载绩效指标
    metrics = None
    equity_curve = None
    if s.status in ("stopped", "failed"):
        payload = _load_metrics_from_db(s.session_id)
        if payload:
            metrics = payload.get("metrics")
            equity_curve = payload.get("equity")

    return {
        "session_id": s.session_id,
        "strategy_id": s.strategy_id,
        "symbols": s.symbols,
        "mode": s.mode,
        "status": s.status,
        "current_date": s.current_date,
        "total_assets": round(s.total_assets, 2) if s.total_assets else None,
        "cash": round(s.cash, 2) if s.cash else None,
        "initial_capital": s.initial_capital if hasattr(s, 'initial_capital') else 1_000_000,
        "positions": list(s.positions),
        "recent_trades": list(s.recent_trades),
        "elapsed_seconds": round(s.elapsed_seconds, 1),
        "metrics": metrics,
        "equity_curve": equity_curve,
        "error": s.error,
        "started_at": s.started_at.isoformat(),
    }
