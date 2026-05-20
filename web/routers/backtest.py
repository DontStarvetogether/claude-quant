"""回测相关 API"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from web.runner import submit_backtest
from web.schemas import (
    BacktestRequest,
    BacktestResultResponse,
    BacktestSubmitResponse,
    HistoryResponse,
    RunStatus,
    RunSummary,
)
from web.serializers import serialize_result
from web.store import run_store

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/run", response_model=BacktestSubmitResponse, status_code=202)
async def run_backtest(req: BacktestRequest) -> BacktestSubmitResponse:
    """提交回测任务，立即返回 run_id。"""
    record = run_store.create(
        strategy_name=req.strategy_id,
        symbols=req.symbols,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
    )

    submit_backtest(
        run_id=record.run_id,
        strategy_id=req.strategy_id,
        symbols=req.symbols,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
        strategy_params=req.strategy_params,
        risk_params=req.risk.model_dump(),
        slippage=req.slippage,
        adjust=req.adjust,
        benchmark=req.benchmark,
    )

    return BacktestSubmitResponse(run_id=record.run_id, status="pending")


@router.get("/{run_id}/status", response_model=RunStatus)
async def get_status(run_id: str) -> RunStatus:
    """查询回测运行状态（SSE 断线时的备用方案）。"""
    record = run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")

    return RunStatus(
        run_id=run_id,
        status=record.status,
        progress=record.progress,
        current_date=record.current_date,
        total_assets=record.total_assets,
        elapsed_seconds=record.elapsed_seconds,
        error=record.error,
    )


@router.get("/{run_id}/stream")
async def stream_progress(run_id: str) -> StreamingResponse:
    """SSE 流式进度推送。"""
    record = run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")

    async def event_generator() -> AsyncIterator[str]:
        last_progress = -1
        last_date: str | None = None

        while True:
            rec = run_store.get(run_id)
            if rec is None:
                break

            if rec.status == "running" and (
                rec.progress != last_progress or rec.current_date != last_date
            ):
                last_progress = rec.progress
                last_date = rec.current_date
                data = json.dumps({
                    "progress": rec.progress,
                    "current_date": rec.current_date,
                    "total_assets": rec.total_assets,
                    "elapsed_seconds": round(rec.elapsed_seconds, 1),
                }, ensure_ascii=False)
                yield f"event: progress\ndata: {data}\n\n"

            elif rec.status == "completed":
                data = json.dumps({
                    "run_id": run_id,
                    "redirect": f"/result.html?run_id={run_id}",
                }, ensure_ascii=False)
                yield f"event: completed\ndata: {data}\n\n"
                break

            elif rec.status == "failed":
                data = json.dumps({"message": rec.error or "回测失败"}, ensure_ascii=False)
                yield f"event: error\ndata: {data}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{run_id}/result", response_model=BacktestResultResponse)
async def get_result(run_id: str) -> BacktestResultResponse:
    """获取完整回测结果。"""
    record = run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
    if record.status != "completed":
        raise HTTPException(status_code=400, detail=f"回测尚未完成（状态: {record.status}）")

    return serialize_result(record)


@router.get("/history/list", response_model=HistoryResponse)
async def get_history() -> HistoryResponse:
    """列出本次进程内所有历史运行记录（摘要）。"""
    runs = []
    for rec in run_store.all():
        metrics = rec.result.metrics if rec.result else None
        runs.append(RunSummary(
            run_id=rec.run_id,
            strategy_name=rec.strategy_name,
            symbols=rec.symbols,
            start_date=rec.start_date,
            end_date=rec.end_date,
            status=rec.status,
            total_return=metrics.total_return if metrics else None,
            sharpe_ratio=metrics.sharpe_ratio if metrics else None,
            created_at=rec.created_at.isoformat(),
        ))
    return HistoryResponse(runs=runs)


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str) -> None:
    """从内存中删除运行记录。"""
    if not run_store.delete(run_id):
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
