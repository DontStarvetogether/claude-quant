from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

os.environ["CQ_DISABLE_DATA_UPDATE"] = "1"

from web.app import app  # noqa: E402


@dataclass(frozen=True)
class AsgiResponse:
    status_code: int
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def test_web_static_pages_and_core_api_smoke():
    assert _get("/").status_code == 200
    assert _get("/docs").status_code == 200
    assert _get("/research.html").status_code == 200
    assert _get("/research_result.html").status_code == 200
    assert _get("/benchmark.html").status_code == 200
    assert _get("/validation.html").status_code == 200

    health = _get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    version = _get("/api/version")
    assert version.status_code == 200
    version_payload = version.json()
    assert version_payload["app"] == "Claude Quant"
    assert version_payload["version"]
    assert version_payload["engine_version"]

    runtime = _get("/api/runtime")
    assert runtime.status_code == 200
    runtime_payload = runtime.json()
    assert runtime_payload["data_update_enabled"] is False
    assert runtime_payload["available_modules"]["backtest"] is True
    assert runtime_payload["available_modules"]["research"] is True
    assert runtime_payload["available_modules"]["benchmark"] is True
    assert runtime_payload["available_modules"]["live"] is True

    history = _get("/api/backtest/history/list")
    assert history.status_code == 200
    assert "runs" in history.json()

    quality = _get("/api/data/quality/summary")
    assert quality.status_code == 200
    assert "market_data" in quality.json()

    research_presets = _get("/api/research/presets")
    assert research_presets.status_code == 200
    assert "factors" in research_presets.json()

    research_universes = _get("/api/research/universes")
    assert research_universes.status_code == 200
    assert "universes" in research_universes.json()

    benchmark_history = _get("/api/benchmark/history/list")
    assert benchmark_history.status_code == 200
    assert "runs" in benchmark_history.json()


def _get(path: str) -> AsgiResponse:
    return asyncio.run(_asgi_get(path))


async def _asgi_get(path: str) -> AsgiResponse:
    status_code = 500
    chunks: list[bytes] = []
    sent_request = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if sent_request:
            return {"type": "http.disconnect"}
        sent_request = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return AsgiResponse(status_code=status_code, body=b"".join(chunks))
