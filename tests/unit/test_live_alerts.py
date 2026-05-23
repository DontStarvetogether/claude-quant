from __future__ import annotations

import json
from datetime import datetime

from cq.live import (
    AlertEvent,
    AlertLevel,
    AlertManager,
    InMemoryAlertSink,
    JsonlAlertSink,
    WebhookAlertSink,
)


def test_alert_event_serializes_stable_payload():
    event = AlertEvent(
        level=AlertLevel.ERROR,
        title="Order failed",
        message="broker rejected order",
        source="qmt",
        session_id="session-1",
        created_at=datetime(2024, 1, 2, 9, 30),
        metadata={"symbol": "600519.SH"},
    )

    payload = event.to_dict()

    assert payload["schema_version"] == "live_alert.v1"
    assert payload["level"] == "ERROR"
    assert payload["created_at"] == "2024-01-02T09:30:00"
    assert payload["metadata"] == {"symbol": "600519.SH"}


def test_alert_manager_fans_out_to_memory_and_jsonl_sinks(tmp_path):
    memory = InMemoryAlertSink()
    jsonl_path = tmp_path / "alerts.jsonl"
    manager = AlertManager([memory, JsonlAlertSink(jsonl_path)])

    event = manager.send(
        level="CRITICAL",
        title="Kill switch",
        message="daily loss limit reached",
        session_id="session-1",
        metadata={"loss_pct": 0.06},
    )

    assert memory.events() == [event]
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["level"] == "CRITICAL"
    assert rows[0]["title"] == "Kill switch"
    assert rows[0]["metadata"] == {"loss_pct": 0.06}


def test_webhook_alert_sink_posts_json_payload(monkeypatch):
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b"ok"

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr("cq.live.alerts.urlopen", fake_urlopen)
    sink = WebhookAlertSink(
        "https://example.test/webhook",
        timeout=3.0,
        headers={"X-Test": "yes"},
    )

    sink.send(
        AlertEvent(
            level=AlertLevel.WARNING,
            title="Risk warning",
            message="cash below target",
            session_id="session-1",
            created_at=datetime(2024, 1, 2, 9, 30),
        )
    )

    assert captured["url"] == "https://example.test/webhook"
    assert captured["timeout"] == 3.0
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["headers"]["X-test"] == "yes"
    assert captured["payload"]["text"] == "[WARNING] Risk warning\ncash below target"
    assert captured["payload"]["event"]["session_id"] == "session-1"
