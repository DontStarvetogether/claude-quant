from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import pandas as pd

os.environ["CQ_DISABLE_DATA_UPDATE"] = "1"

from web.app import app  # noqa: E402
from web.research_runner import run_factor_research  # noqa: E402
from web.research_store import ResearchRunStore  # noqa: E402
from web.routers import research as research_router  # noqa: E402


@dataclass(frozen=True)
class AsgiResponse:
    status_code: int
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def test_research_api_runs_factor_job_and_serves_artifacts(tmp_path, monkeypatch):
    store = ResearchRunStore(tmp_path / "research.db")

    def submit_immediately(run_id: str, request: dict[str, Any]) -> None:
        run_factor_research(run_id, request, store=store)

    monkeypatch.setattr(research_router, "research_store", store)
    monkeypatch.setattr(research_router, "submit_factor_research", submit_immediately)

    price_csv = tmp_path / "prices.csv"
    _price_frame().to_csv(price_csv, index=False)

    presets = _get("/api/research/presets")
    assert presets.status_code == 200
    assert any(item["id"] == "momentum_20d" for item in presets.json()["factors"])

    universes = _get("/api/research/universes")
    assert universes.status_code == 200
    assert any(item["id"] == "core50" for item in universes.json()["universes"])

    response = _post_json(
        "/api/research/run",
        {
            "factor_id": "momentum_20d",
            "universe_id": "core50",
            "price_csv": str(price_csv),
            "start_date": "2024-02-01",
            "end_date": "2024-03-15",
            "forward_periods": [1, 5],
            "groups": 3,
            "ic_method": "spearman",
            "rebalance": "weekly",
            "sample_split_date": "2024-02-20",
            "output_dir": str(tmp_path / "research_output"),
        },
    )

    assert response.status_code == 202
    run_id = response.json()["run_id"]

    status = _get(f"/api/research/{run_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed", status.json().get("error")

    result = _get(f"/api/research/{run_id}/result")
    assert result.status_code == 200
    payload = result.json()
    assert payload["factor_id"] == "momentum_20d"
    assert payload["summary"]["factor_name"] == "20日动量"
    assert payload["tables"]["ic_summary"]
    assert payload["artifacts"]["report"].endswith(f"/api/research/{run_id}/artifact/report.md")

    artifact = _get(f"/api/research/{run_id}/artifact/report.md")
    assert artifact.status_code == 200
    assert artifact.body.startswith(b"# ")


def _get(path: str) -> AsgiResponse:
    return asyncio.run(_asgi_request("GET", path))


def _post_json(path: str, payload: dict[str, Any]) -> AsgiResponse:
    return asyncio.run(_asgi_request("POST", path, payload))


async def _asgi_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> AsgiResponse:
    status_code = 500
    chunks: list[bytes] = []
    sent_request = False
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if sent_request:
            return {"type": "http.disconnect"}
        sent_request = True
        return {"type": "http.request", "body": body, "more_body": False}

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
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return AsgiResponse(status_code=status_code, body=b"".join(chunks))


def _price_frame() -> pd.DataFrame:
    symbols = ["600519.SH", "000858.SZ", "600036.SH", "601318.SH", "600276.SH"]
    dates = pd.bdate_range("2024-01-01", periods=70)
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        for day_index, trade_date in enumerate(dates):
            drift = 1 + symbol_index * 0.003
            close = 100 + day_index * drift + symbol_index
            rows.append({"date": trade_date.date().isoformat(), "symbol": symbol, "close": close})
    return pd.DataFrame(rows)
