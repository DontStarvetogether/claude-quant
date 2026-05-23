"""Restart recovery state for paper/live trading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "live_recovery.v1"


@dataclass(frozen=True)
class LiveRecoveryState:
    """Serializable state needed to resume a live/paper session safely."""

    session_id: str
    status: str
    updated_at: datetime
    idempotency_keys: tuple[str, ...] = ()
    pending_plan_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "status": self.status,
            "updated_at": self.updated_at.isoformat(),
            "idempotency_keys": list(self.idempotency_keys),
            "pending_plan_ids": list(self.pending_plan_ids),
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LiveRecoveryState:
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported recovery schema: {payload.get('schema_version')}")
        return cls(
            session_id=str(payload["session_id"]),
            status=str(payload["status"]),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
            idempotency_keys=tuple(str(key) for key in payload.get("idempotency_keys", [])),
            pending_plan_ids=tuple(str(plan_id) for plan_id in payload.get("pending_plan_ids", [])),
            metadata=dict(payload.get("metadata", {}) or {}),
        )


class LiveRecoveryStore:
    """Persist and load recovery state snapshots as JSON files."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, state: LiveRecoveryState) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(state.session_id)
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def load(self, session_id: str) -> LiveRecoveryState | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        return LiveRecoveryState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_states(self) -> list[LiveRecoveryState]:
        if not self._root.exists():
            return []
        states = []
        for path in sorted(self._root.glob("*.json")):
            states.append(LiveRecoveryState.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        return sorted(states, key=lambda state: state.updated_at, reverse=True)

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in {"-", "_"})
        if not safe:
            raise ValueError("session_id must contain at least one safe character")
        return self._root / f"{safe}.json"
