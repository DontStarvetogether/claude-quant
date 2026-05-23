"""Alert primitives for paper/live trading."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from urllib.request import Request, urlopen

SCHEMA_VERSION = "live_alert.v1"


class AlertLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class AlertEvent:
    """Structured alert event."""

    level: AlertLevel
    title: str
    message: str
    source: str = "live"
    session_id: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        payload["level"] = self.level.value
        payload["created_at"] = (self.created_at or datetime.now()).isoformat()
        payload["metadata"] = dict(self.metadata or {})
        return payload


class AlertSink(Protocol):
    def send(self, event: AlertEvent) -> None:
        """Send one alert event."""
        ...


class InMemoryAlertSink:
    """Collect alerts in memory for tests and web status views."""

    def __init__(self) -> None:
        self._events: list[AlertEvent] = []
        self._lock = Lock()

    def send(self, event: AlertEvent) -> None:
        with self._lock:
            self._events.append(event)

    def events(self) -> list[AlertEvent]:
        with self._lock:
            return list(self._events)


class JsonlAlertSink:
    """Persist alerts as newline-delimited JSON."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()

    def send(self, event: AlertEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class WebhookAlertSink:
    """Send alerts to a generic JSON webhook endpoint."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 5.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json", **dict(headers or {})}

    def send(self, event: AlertEvent) -> None:
        payload = {
            "text": f"[{event.level.value}] {event.title}\n{event.message}",
            "event": event.to_dict(),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self._url,
            data=data,
            headers=self._headers,
            method="POST",
        )
        with urlopen(request, timeout=self._timeout) as response:
            response.read()


class AlertManager:
    """Fan out alerts to configured sinks."""

    def __init__(self, sinks: Sequence[AlertSink] | None = None) -> None:
        self._sinks = list(sinks or [])

    def add_sink(self, sink: AlertSink) -> None:
        self._sinks.append(sink)

    def send(
        self,
        *,
        level: AlertLevel | str,
        title: str,
        message: str,
        source: str = "live",
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AlertEvent:
        event = AlertEvent(
            level=AlertLevel(level),
            title=title,
            message=message,
            source=source,
            session_id=session_id,
            created_at=datetime.now(),
            metadata=metadata,
        )
        for sink in self._sinks:
            sink.send(event)
        return event
