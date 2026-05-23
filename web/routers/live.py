"""实盘/模拟盘相关 API"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from cq.core.models import OrderSide, OrderType
from cq.live import OrderIntent, TradePlan
from cq.strategy.registry import validate_strategy_params
from web import db
from web.live_runner import (
    get_trade_plan_store,
    list_daily_report_snapshots,
    list_recovery_snapshots,
    live_store,
    load_daily_report_snapshot,
    load_recovery_snapshot,
    start_live_session,
    start_paper_session,
    stop_session,
)
from web.schemas import (
    LiveSessionsResponse,
    LiveSessionStatus,
    LiveSessionSummary,
    LiveStartRequest,
    LiveStartResponse,
    TradePlanCreateRequest,
    TradePlanListResponse,
    TradePlanResponse,
    TradePlanReviewRequest,
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
        _validate_live_trade_plan(req)
        session = start_live_session(
            strategy_id=req.strategy_id,
            symbols=req.symbols,
            initial_capital=req.initial_capital,
            strategy_params=strategy_params,
            risk_params=req.risk.model_dump(),
            account_id=req.account_id,
            mini_qmt_dir=req.mini_qmt_dir,
            trade_plan_id=req.trade_plan_id,
            kill_switch_enabled=req.kill_switch_enabled,
            kill_switch_reason=req.kill_switch_reason,
            daily_loss_limit_pct=req.daily_loss_limit_pct,
            daily_loss_limit_amount=req.daily_loss_limit_amount,
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


@router.post("/plans", response_model=TradePlanResponse, status_code=201)
async def create_trade_plan(req: TradePlanCreateRequest) -> TradePlanResponse:
    """创建待人工确认的交易计划。"""
    plan_id = req.plan_id or f"plan-{uuid.uuid4().hex[:12]}"
    trade_date = date.fromisoformat(req.trade_date)
    orders = tuple(
        OrderIntent(
            namespace=order.namespace,
            trade_date=date.fromisoformat(order.trade_date),
            symbol=order.symbol.upper(),
            side=OrderSide(order.side),
            order_type=OrderType(order.order_type),
            quantity=order.quantity,
            limit_price=order.limit_price,
            percent=order.percent,
            amount=order.amount,
        )
        for order in req.orders
    )
    plan = TradePlan(
        plan_id=plan_id,
        trade_date=trade_date,
        strategy_id=req.strategy_id,
        account_id=req.account_id,
        orders=orders,
        generated_at=datetime.now(),
    )
    store = get_trade_plan_store()
    store.save(plan)
    return TradePlanResponse(plan=plan.to_dict())


@router.get("/plans", response_model=TradePlanListResponse)
async def list_trade_plans(status: str | None = None) -> TradePlanListResponse:
    """列出交易计划。"""
    store = get_trade_plan_store()
    return TradePlanListResponse(plans=[plan.to_dict() for plan in store.list_plans(status=status)])


@router.get("/plans/{plan_id}", response_model=TradePlanResponse)
async def get_trade_plan(plan_id: str) -> TradePlanResponse:
    """查询单个交易计划。"""
    plan = get_trade_plan_store().load(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"trade plan {plan_id} 不存在")
    return TradePlanResponse(plan=plan.to_dict())


@router.post("/plans/{plan_id}/approve", response_model=TradePlanResponse)
async def approve_trade_plan(plan_id: str, req: TradePlanReviewRequest) -> TradePlanResponse:
    """批准交易计划。"""
    try:
        plan = get_trade_plan_store().approve(plan_id, reviewer=req.reviewer)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"trade plan {plan_id} 不存在") from None
    return TradePlanResponse(plan=plan.to_dict())


@router.post("/plans/{plan_id}/reject", response_model=TradePlanResponse)
async def reject_trade_plan(plan_id: str, req: TradePlanReviewRequest) -> TradePlanResponse:
    """拒绝交易计划。"""
    try:
        plan = get_trade_plan_store().reject(plan_id, reviewer=req.reviewer, reason=req.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"trade plan {plan_id} 不存在") from None
    return TradePlanResponse(plan=plan.to_dict())


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
        with suppress(json.JSONDecodeError, TypeError):
            positions = json.loads(d["final_positions"])

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


@router.get("/recovery")
async def list_recovery() -> dict:
    """列出所有恢复状态快照。"""
    return {"states": list_recovery_snapshots()}


@router.get("/daily-reports")
async def list_daily_reports() -> dict:
    """列出所有每日交易日报摘要。"""
    return {"reports": list_daily_report_snapshots()}


@router.get("/{session_id}/trades")
async def get_trades(session_id: str) -> list[dict]:
    """获取会话的所有成交记录。"""
    return db.get_trades(session_id)


@router.get("/{session_id}/equity")
async def get_equity(session_id: str) -> list[dict]:
    """获取会话的净值曲线数据。"""
    return db.get_equity_curve(session_id)


@router.get("/{session_id}/recovery")
async def get_recovery(session_id: str) -> dict:
    """获取会话的恢复状态快照。"""
    snapshot = load_recovery_snapshot(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 恢复状态不存在")
    return snapshot


@router.get("/{session_id}/daily-report")
async def get_daily_report(session_id: str) -> dict:
    """获取会话的每日交易日报。"""
    report = load_daily_report_snapshot(session_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 交易日报不存在")
    return report


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


def _validate_live_trade_plan(req: LiveStartRequest) -> None:
    """Require an approved trade plan for real live sessions by default."""
    require_plan = req.require_trade_plan if req.require_trade_plan is not None else True
    if not require_plan:
        return
    if not req.trade_plan_id:
        raise HTTPException(status_code=400, detail="实盘模式需要提供已批准的 trade_plan_id")
    plan = get_trade_plan_store().load(req.trade_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"trade plan {req.trade_plan_id} 不存在")
    if plan.account_id != req.account_id:
        raise HTTPException(status_code=400, detail="trade_plan_id 的 account_id 与请求不一致")
    try:
        plan.require_approved()
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


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
