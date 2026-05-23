"""因子研究 Web API。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from web.research_runner import factor_name, list_factor_presets, submit_factor_research
from web.research_store import research_store
from web.schemas import (
    FactorPresetsResponse,
    FactorResearchHistoryResponse,
    FactorResearchRequest,
    FactorResearchResultResponse,
    FactorResearchRunSummary,
    FactorResearchStatus,
    FactorResearchSubmitResponse,
)
from web.universe_registry import get_universe_presets

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/presets", response_model=FactorPresetsResponse)
async def get_presets() -> FactorPresetsResponse:
    """List built-in factor presets exposed to the Web workbench."""

    return FactorPresetsResponse(factors=list_factor_presets())


@router.get("/universes")
async def get_research_universes() -> dict[str, list[dict[str, object]]]:
    """List universes with compact quality metadata for research forms."""

    universes: list[dict[str, object]] = []
    for item in get_universe_presets():
        entry = dict(item)
        symbols = entry.get("symbols") or []
        entry["symbol_count"] = len(symbols) if isinstance(symbols, list) else 0
        entry["quality"] = (
            "best_effort_static"
            if entry.get("construction") == "static"
            else str(entry.get("construction") or "unknown")
        )
        universes.append(entry)

    universes.extend([
        {
            "id": "HS300_PIT",
            "name": "沪深300 PIT",
            "source": "pit_csv",
            "construction": "point_in_time",
            "quality": "strict_historical_pit_if_csv_provided",
            "symbol_count": 0,
            "symbols": [],
        },
        {
            "id": "ZZ500_PIT",
            "name": "中证500 PIT",
            "source": "pit_csv",
            "construction": "point_in_time",
            "quality": "strict_historical_pit_if_csv_provided",
            "symbol_count": 0,
            "symbols": [],
        },
        {
            "id": "ZZ1000_PIT",
            "name": "中证1000 PIT",
            "source": "pit_csv",
            "construction": "point_in_time",
            "quality": "strict_historical_pit_if_csv_provided",
            "symbol_count": 0,
            "symbols": [],
        },
    ])
    return {"universes": universes}


@router.post("/run", response_model=FactorResearchSubmitResponse, status_code=202)
async def run_research(req: FactorResearchRequest) -> FactorResearchSubmitResponse:
    """Submit a factor research job and return immediately."""

    try:
        name = factor_name(req.factor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if req.start_date > req.end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    request_payload = req.model_dump(mode="json")
    output_root = req.output_dir or "output/research"
    record = research_store.create(
        factor_id=req.factor_id,
        factor_name=name,
        universe_id=req.universe_id,
        start_date=req.start_date.isoformat(),
        end_date=req.end_date.isoformat(),
        request=request_payload,
        output_dir=output_root,
    )
    submit_factor_research(record.run_id, request_payload)
    return FactorResearchSubmitResponse(run_id=record.run_id, status="pending")


@router.get("/{run_id}/status", response_model=FactorResearchStatus)
async def get_status(run_id: str) -> FactorResearchStatus:
    """Get factor research run status."""

    record = research_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
    return FactorResearchStatus(
        run_id=record.run_id,
        status=record.status,
        progress=record.progress,
        current_step=record.current_step,
        elapsed_seconds=record.elapsed_seconds,
        error=record.error,
    )


@router.get("/{run_id}/stream")
async def stream_progress(run_id: str) -> StreamingResponse:
    """Stream factor research progress as SSE."""

    if research_store.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")

    async def event_generator() -> AsyncIterator[str]:
        last_progress = -1
        last_step: str | None = None
        while True:
            record = research_store.get(run_id)
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
                data = json.dumps({
                    "run_id": run_id,
                    "redirect": f"/research_result.html?run_id={run_id}",
                }, ensure_ascii=False)
                yield f"event: completed\ndata: {data}\n\n"
                break
            elif record.status == "failed":
                data = json.dumps({"message": record.error or "因子研究失败"}, ensure_ascii=False)
                yield f"event: error\ndata: {data}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/result", response_model=FactorResearchResultResponse)
async def get_result(run_id: str) -> FactorResearchResultResponse:
    """Get complete factor research result."""

    record = research_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
    if record.status != "completed" or record.result is None:
        raise HTTPException(status_code=400, detail=f"因子研究尚未完成（状态: {record.status}）")

    return FactorResearchResultResponse(
        run_id=record.run_id,
        status=record.status,
        factor_id=record.factor_id,
        factor_name=record.factor_name,
        universe_id=record.universe_id,
        start_date=record.start_date,
        end_date=record.end_date,
        summary=record.result.get("summary", {}),
        diagnostics=record.result.get("diagnostics", {}),
        artifacts=_artifact_urls(run_id, record.artifacts),
        tables=record.result.get("tables", {}),
        request=record.request,
        created_at=record.created_at.isoformat(),
    )


@router.get("/{run_id}/artifacts")
async def get_artifacts(run_id: str) -> dict[str, str]:
    """List artifact URLs for a run."""

    record = research_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")
    return _artifact_urls(run_id, record.artifacts)


@router.get("/{run_id}/artifact/{name}")
async def get_artifact(run_id: str, name: str) -> FileResponse:
    """Download or preview one factor research artifact."""

    record = research_store.get(run_id)
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


@router.get("/history/list", response_model=FactorResearchHistoryResponse)
async def get_history() -> FactorResearchHistoryResponse:
    """List research history."""

    runs = [
        FactorResearchRunSummary(
            run_id=record.run_id,
            factor_id=record.factor_id,
            factor_name=record.factor_name,
            universe_id=record.universe_id,
            start_date=record.start_date,
            end_date=record.end_date,
            status=record.status,
            created_at=record.created_at.isoformat(),
        )
        for record in research_store.all()
    ]
    return FactorResearchHistoryResponse(runs=runs)


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str) -> None:
    """Delete a research run record."""

    if not research_store.delete(run_id):
        raise HTTPException(status_code=404, detail=f"run_id {run_id} 不存在")


def _artifact_urls(run_id: str, artifacts: dict[str, str]) -> dict[str, str]:
    return {
        name: f"/api/research/{run_id}/artifact/{Path(path).name}"
        for name, path in artifacts.items()
    }
