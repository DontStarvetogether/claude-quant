"""Benchmark Web API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from web.benchmark_runner import submit_benchmark
from web.benchmark_store import benchmark_store
from web.schemas import (
    BenchmarkHistoryResponse,
    BenchmarkResultResponse,
    BenchmarkRunRequest,
    BenchmarkRunSummary,
    BenchmarkStatus,
    BenchmarkSubmitResponse,
)

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])


@router.post("/run", response_model=BenchmarkSubmitResponse, status_code=202)
async def run_benchmark(req: BenchmarkRunRequest) -> BenchmarkSubmitResponse:
    """Submit a benchmark job."""

    if req.start_date and req.end_date and req.start_date > req.end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    request_payload = req.model_dump(mode="json")
    record = benchmark_store.create(
        name="20日动量 TopN Benchmark",
        universe_id=req.universe_id,
        request=request_payload,
        output_dir=req.output_dir or "output/benchmark",
    )
    submit_benchmark(record.run_id, request_payload)
    return BenchmarkSubmitResponse(run_id=record.run_id, status="pending")


@router.get("/{run_id}/status", response_model=BenchmarkStatus)
async def get_status(run_id: str) -> BenchmarkStatus:
    record = benchmark_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
    return BenchmarkStatus(
        run_id=record.run_id,
        status=record.status,
        progress=record.progress,
        current_step=record.current_step,
        elapsed_seconds=record.elapsed_seconds,
        error=record.error,
    )


@router.get("/{run_id}/stream")
async def stream_progress(run_id: str) -> StreamingResponse:
    if benchmark_store.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")

    async def event_generator() -> AsyncIterator[str]:
        last_progress = -1
        last_step: str | None = None
        while True:
            record = benchmark_store.get(run_id)
            if record is None:
                break
            if record.status == "running" and (
                record.progress != last_progress or record.current_step != last_step
            ):
                last_progress = record.progress
                last_step = record.current_step
                data = json.dumps({
                    "progress": record.progress,
                    "current_step": record.current_step,
                    "message": record.current_step,
                    "elapsed_seconds": round(record.elapsed_seconds, 1),
                }, ensure_ascii=False)
                yield f"event: progress\ndata: {data}\n\n"
            elif record.status == "completed":
                data = json.dumps({"run_id": run_id}, ensure_ascii=False)
                yield f"event: completed\ndata: {data}\n\n"
                break
            elif record.status == "failed":
                data = json.dumps({"message": record.error or "benchmark 失败"}, ensure_ascii=False)
                yield f"event: error\ndata: {data}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/result", response_model=BenchmarkResultResponse)
async def get_result(run_id: str) -> BenchmarkResultResponse:
    record = benchmark_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
    if record.status != "completed" or record.result is None:
        raise HTTPException(status_code=400, detail=f"benchmark 尚未完成（状态: {record.status}）")
    return BenchmarkResultResponse(
        run_id=record.run_id,
        status=record.status,
        name=record.name,
        universe_id=record.universe_id,
        summary=record.result.get("summary", {}),
        diagnostics=record.result.get("diagnostics", {}),
        artifacts=_artifact_urls(run_id, record.artifacts),
        tables=record.result.get("tables", {}),
        request=record.request,
        created_at=record.created_at.isoformat(),
    )


@router.get("/{run_id}/artifacts")
async def get_artifacts(run_id: str) -> dict[str, str]:
    record = benchmark_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
    return _artifact_urls(run_id, record.artifacts)


@router.get("/{run_id}/artifact/{name}")
async def get_artifact(run_id: str, name: str) -> FileResponse:
    record = benchmark_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
    path_raw = record.artifacts.get(name)
    if path_raw is None:
        path_raw = next(
            (
                raw
                for key, raw in record.artifacts.items()
                if Path(raw).name == name or Path(raw).stem == name or key == Path(name).stem
            ),
            None,
        )
    if path_raw is None:
        raise HTTPException(status_code=404, detail=f"artifact {name} 不存在")
    path = Path(path_raw)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"artifact file {name} 不存在")
    media_type = "text/markdown" if path.suffix == ".md" else "text/csv"
    if path.suffix == ".json":
        media_type = "application/json"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/history/list", response_model=BenchmarkHistoryResponse)
async def get_history() -> BenchmarkHistoryResponse:
    return BenchmarkHistoryResponse(
        runs=[
            BenchmarkRunSummary(
                run_id=record.run_id,
                name=record.name,
                universe_id=record.universe_id,
                status=record.status,
                created_at=record.created_at.isoformat(),
            )
            for record in benchmark_store.all()
        ]
    )


def _artifact_urls(run_id: str, artifacts: dict[str, str]) -> dict[str, str]:
    return {
        name: f"/api/benchmark/{run_id}/artifact/{Path(path).name}"
        for name, path in artifacts.items()
    }
