"""因子研究运行记录持久化存储。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_DEFAULT_DB_PATH = Path(os.getenv("CQ_RESEARCH_DB", "data/research_runs.db"))


@dataclass
class ResearchRunRecord:
    run_id: str
    factor_id: str
    factor_name: str
    universe_id: str
    start_date: str
    end_date: str
    status: str = "pending"
    progress: int = 0
    current_step: str | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None
    request: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    output_dir: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


class ResearchRunStore:
    """SQLite storage for factor research jobs and exported artifact metadata."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else _DEFAULT_DB_PATH
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id          TEXT PRIMARY KEY,
                    factor_id       TEXT NOT NULL,
                    factor_name     TEXT NOT NULL,
                    universe_id     TEXT NOT NULL,
                    start_date      TEXT NOT NULL,
                    end_date        TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    progress        INTEGER NOT NULL DEFAULT 0,
                    current_step    TEXT,
                    elapsed_seconds REAL NOT NULL DEFAULT 0,
                    error           TEXT,
                    request_json    TEXT,
                    result_json     TEXT,
                    artifacts_json  TEXT,
                    output_dir      TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_research_runs_created
                    ON research_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_runs_status
                    ON research_runs(status);
            """)

    def create(
        self,
        *,
        factor_id: str,
        factor_name: str,
        universe_id: str,
        start_date: str,
        end_date: str,
        request: dict[str, Any],
        output_dir: str | None = None,
    ) -> ResearchRunRecord:
        run_id = str(uuid.uuid4())
        now = datetime.now()
        record = ResearchRunRecord(
            run_id=run_id,
            factor_id=factor_id,
            factor_name=factor_name,
            universe_id=universe_id,
            start_date=start_date,
            end_date=end_date,
            request=request,
            output_dir=output_dir,
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO research_runs
                   (run_id, factor_id, factor_name, universe_id, start_date, end_date,
                    status, progress, current_step, elapsed_seconds, error, request_json,
                    result_json, artifacts_json, output_dir, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.run_id,
                    record.factor_id,
                    record.factor_name,
                    record.universe_id,
                    record.start_date,
                    record.end_date,
                    record.status,
                    record.progress,
                    record.current_step,
                    record.elapsed_seconds,
                    record.error,
                    json.dumps(record.request, ensure_ascii=False),
                    None,
                    json.dumps(record.artifacts, ensure_ascii=False),
                    record.output_dir,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get(self, run_id: str) -> ResearchRunRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM research_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def all(self, limit: int = 100) -> list[ResearchRunRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def update_status(
        self,
        run_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        elapsed_seconds: float | None = None,
        error: str | None = None,
    ) -> None:
        assignments: list[str] = ["updated_at=?"]
        values: list[object] = [datetime.now().isoformat()]
        fields = {
            "status": status,
            "progress": progress,
            "current_step": current_step,
            "elapsed_seconds": elapsed_seconds,
            "error": error,
        }
        for name, value in fields.items():
            if value is not None:
                assignments.append(f"{name}=?")
                values.append(value)
        values.append(run_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE research_runs SET {', '.join(assignments)} WHERE run_id=?",
                values,
            )

    def save_result(
        self,
        run_id: str,
        *,
        result: dict[str, Any],
        artifacts: dict[str, str],
        output_dir: str,
        elapsed_seconds: float,
    ) -> None:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE research_runs
                   SET status='completed', progress=100, current_step='完成',
                       result_json=?, artifacts_json=?, output_dir=?, error=NULL,
                       elapsed_seconds=?, updated_at=?
                   WHERE run_id=?""",
                (
                    json.dumps(result, ensure_ascii=False),
                    json.dumps(artifacts, ensure_ascii=False),
                    output_dir,
                    elapsed_seconds,
                    now,
                    run_id,
                ),
            )

    def save_error(self, run_id: str, error: str, elapsed_seconds: float) -> None:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE research_runs
                   SET status='failed', progress=100, error=?, elapsed_seconds=?, updated_at=?
                   WHERE run_id=?""",
                (error, elapsed_seconds, now, run_id),
            )

    def delete(self, run_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM research_runs WHERE run_id=?", (run_id,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ResearchRunRecord:
        created_at = datetime.fromisoformat(row["created_at"])
        updated_at = (
            datetime.fromisoformat(row["updated_at"])
            if row["updated_at"]
            else created_at
        )
        return ResearchRunRecord(
            run_id=row["run_id"],
            factor_id=row["factor_id"],
            factor_name=row["factor_name"],
            universe_id=row["universe_id"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            status=row["status"],
            progress=row["progress"],
            current_step=row["current_step"],
            elapsed_seconds=row["elapsed_seconds"],
            error=row["error"],
            request=json.loads(row["request_json"] or "{}"),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            artifacts=json.loads(row["artifacts_json"] or "{}"),
            output_dir=row["output_dir"],
            created_at=created_at,
            updated_at=updated_at,
        )


research_store = ResearchRunStore()
