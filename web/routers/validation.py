"""Cross-platform validation Web API."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from cq.benchmark import (
    CrossValidationInputFiles,
    CrossValidationTolerance,
    compare_benchmark_with_external,
    export_cross_validation_result,
    export_cross_validation_template,
    load_cross_validation_frames,
)
from web.schemas import (
    CrossValidationRunRequest,
    ValidationArtifactResponse,
    ValidationTemplateRequest,
)

router = APIRouter(prefix="/api/validation", tags=["validation"])

_ARTIFACTS: dict[str, dict[str, str]] = {}


@router.post("/template", response_model=ValidationArtifactResponse)
async def create_template(req: ValidationTemplateRequest) -> ValidationArtifactResponse:
    """Export external-platform cross-validation templates."""

    artifact_set_id = str(uuid.uuid4())
    output_dir = Path(req.output_dir or "output/cross_validation/templates") / artifact_set_id
    exported = export_cross_validation_template(output_dir, platform_name=req.platform_name)
    artifacts = {key: str(path) for key, path in exported.files.items()}
    _ARTIFACTS[artifact_set_id] = artifacts
    return ValidationArtifactResponse(
        artifact_set_id=artifact_set_id,
        summary=exported.summary,
        artifacts=_artifact_urls(artifact_set_id, artifacts),
    )


@router.post("/run", response_model=ValidationArtifactResponse)
async def run_validation(req: CrossValidationRunRequest) -> ValidationArtifactResponse:
    """Compare local benchmark exports with external platform exports."""

    artifact_set_id = str(uuid.uuid4())
    local = load_cross_validation_frames(
        CrossValidationInputFiles(
            equity_curve=_path(req.local_equity_csv),
            holdings=_optional_path(req.local_holdings_csv),
            trades=_optional_path(req.local_trades_csv),
        ),
        source_name="local",
    )
    external = load_cross_validation_frames(
        CrossValidationInputFiles(
            equity_curve=_path(req.external_equity_csv),
            holdings=_optional_path(req.external_holdings_csv),
            trades=_optional_path(req.external_trades_csv),
        ),
        source_name=req.platform_name,
    )
    result = compare_benchmark_with_external(
        local,
        external,
        CrossValidationTolerance(
            equity_abs=req.equity_abs,
            quantity_abs=req.quantity_abs,
            price_abs=req.price_abs,
            amount_abs=req.amount_abs,
            fee_abs=req.fee_abs,
        ),
        platform_name=req.platform_name,
    )
    output_dir = Path(req.output_dir or "output/cross_validation/reports") / artifact_set_id
    exported = export_cross_validation_result(result, output_dir)
    artifacts = {key: str(path) for key, path in exported.files.items()}
    _ARTIFACTS[artifact_set_id] = artifacts
    return ValidationArtifactResponse(
        artifact_set_id=artifact_set_id,
        summary=exported.summary,
        artifacts=_artifact_urls(artifact_set_id, artifacts),
    )


@router.get("/{artifact_set_id}/artifact/{name}")
async def get_artifact(artifact_set_id: str, name: str) -> FileResponse:
    artifacts = _ARTIFACTS.get(artifact_set_id)
    if artifacts is None:
        raise HTTPException(status_code=404, detail=f"artifact set {artifact_set_id} 不存在")
    path_raw = artifacts.get(name)
    if path_raw is None:
        path_raw = next(
            (
                raw
                for key, raw in artifacts.items()
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


def _path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=400, detail=f"CSV 文件不存在: {path}")
    return path


def _optional_path(value: str | None) -> Path | None:
    return _path(value) if value else None


def _artifact_urls(artifact_set_id: str, artifacts: dict[str, str]) -> dict[str, str]:
    return {
        name: f"/api/validation/{artifact_set_id}/artifact/{Path(path).name}"
        for name, path in artifacts.items()
    }
