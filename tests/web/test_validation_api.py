from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import pandas as pd

os.environ["CQ_DISABLE_DATA_UPDATE"] = "1"

from web.app import app  # noqa: E402


@dataclass(frozen=True)
class AsgiResponse:
    status_code: int
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def test_validation_api_exports_template_and_compares_equity(tmp_path):
    template = _post_json(
        "/api/validation/template",
        {"platform_name": "joinquant", "output_dir": str(tmp_path / "templates")},
    )
    assert template.status_code == 200
    template_payload = template.json()
    assert template_payload["artifacts"]["equity_curve"].endswith("/artifact/equity_curve.csv")

    local_equity = tmp_path / "local_equity.csv"
    external_equity = tmp_path / "external_equity.csv"
    equity = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02"],
            "total_assets": [1_000_000, 1_010_000],
            "cash": [1_000_000, 900_000],
            "position_value": [0, 110_000],
        }
    )
    equity.to_csv(local_equity, index=False)
    equity.to_csv(external_equity, index=False)

    result = _post_json(
        "/api/validation/run",
        {
            "platform_name": "joinquant",
            "local_equity_csv": str(local_equity),
            "external_equity_csv": str(external_equity),
            "output_dir": str(tmp_path / "reports"),
        },
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["summary"]["passed"] is True
    assert payload["artifacts"]["report"].endswith("/artifact/cross_validation_report.md")

    report = _get(payload["artifacts"]["report"])
    assert report.status_code == 200
    assert report.body.startswith(b"# ")


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
