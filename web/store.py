"""回测运行记录持久化存储。"""

from __future__ import annotations

import json
import os
import pickle
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from cq.engine.backtest_engine import BacktestResult


_DEFAULT_DB_PATH = Path(os.getenv("CQ_BACKTEST_DB", "data/backtest.db"))


@dataclass
class RunRecord:
    run_id: str
    strategy_name: str
    symbols: list[str]
    start_date: str
    end_date: str
    initial_capital: float
    status: str = "pending"   # pending | running | completed | failed
    progress: int = 0
    current_date: Optional[str] = None
    total_assets: Optional[float] = None
    elapsed_seconds: float = 0.0
    error: Optional[str] = None
    result: Optional[BacktestResult] = None
    benchmark: Optional[str] = None
    request_json: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


class RunStore:
    """SQLite 存储，保证回测结果在服务重启后仍可读取。"""

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
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id          TEXT PRIMARY KEY,
                    strategy_name   TEXT NOT NULL,
                    symbols_json    TEXT NOT NULL,
                    start_date      TEXT NOT NULL,
                    end_date        TEXT NOT NULL,
                    initial_capital REAL NOT NULL,
                    benchmark       TEXT,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    progress        INTEGER NOT NULL DEFAULT 0,
                    current_date    TEXT,
                    total_assets    REAL,
                    elapsed_seconds REAL NOT NULL DEFAULT 0,
                    error           TEXT,
                    request_json    TEXT,
                    result_blob     BLOB,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_backtest_runs_created
                    ON backtest_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_status
                    ON backtest_runs(status);
            """)

            for col, ddl in [
                ("benchmark", "ALTER TABLE backtest_runs ADD COLUMN benchmark TEXT"),
                ("request_json", "ALTER TABLE backtest_runs ADD COLUMN request_json TEXT"),
                ("updated_at", "ALTER TABLE backtest_runs ADD COLUMN updated_at TEXT"),
            ]:
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass

            conn.execute(
                "UPDATE backtest_runs SET updated_at = COALESCE(updated_at, created_at)"
            )

    def create(
        self,
        strategy_name: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float,
        benchmark: str | None = None,
        request: dict | None = None,
    ) -> RunRecord:
        run_id = str(uuid.uuid4())
        now = datetime.now()
        record = RunRecord(
            run_id=run_id,
            strategy_name=strategy_name,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            benchmark=benchmark,
            request_json=json.dumps(request, ensure_ascii=False) if request else None,
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO backtest_runs
                   (run_id, strategy_name, symbols_json, start_date, end_date,
                    initial_capital, benchmark, status, progress, current_date,
                    total_assets, elapsed_seconds, error, request_json, result_blob,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.run_id,
                    record.strategy_name,
                    json.dumps(record.symbols, ensure_ascii=False),
                    record.start_date,
                    record.end_date,
                    record.initial_capital,
                    record.benchmark,
                    record.status,
                    record.progress,
                    record.current_date,
                    record.total_assets,
                    record.elapsed_seconds,
                    record.error,
                    record.request_json,
                    None,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def all(self, limit: int = 100) -> list[RunRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def update_status(
        self,
        run_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        current_date: str | None = None,
        total_assets: float | None = None,
        elapsed_seconds: float | None = None,
        error: str | None = None,
    ) -> None:
        assignments: list[str] = ["updated_at=?"]
        values: list[object] = [datetime.now().isoformat()]

        fields = {
            "status": status,
            "progress": progress,
            "current_date": current_date,
            "total_assets": total_assets,
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
                f"UPDATE backtest_runs SET {', '.join(assignments)} WHERE run_id=?",
                values,
            )

    def save_result(self, run_id: str, result: BacktestResult, elapsed_seconds: float) -> None:
        blob = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE backtest_runs
                   SET status='completed', progress=100, result_blob=?, error=NULL,
                       elapsed_seconds=?, updated_at=?
                   WHERE run_id=?""",
                (blob, elapsed_seconds, now, run_id),
            )

    def save_error(self, run_id: str, error: str, elapsed_seconds: float) -> None:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE backtest_runs
                   SET status='failed', error=?, elapsed_seconds=?, updated_at=?
                   WHERE run_id=?""",
                (error, elapsed_seconds, now, run_id),
            )

    def delete(self, run_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM backtest_runs WHERE run_id=?", (run_id,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> RunRecord:
        result = None
        blob = row["result_blob"]
        if blob is not None:
            result = pickle.loads(blob)

        created_at = datetime.fromisoformat(row["created_at"])
        updated_at = (
            datetime.fromisoformat(row["updated_at"])
            if row["updated_at"]
            else created_at
        )
        return RunRecord(
            run_id=row["run_id"],
            strategy_name=row["strategy_name"],
            symbols=json.loads(row["symbols_json"]),
            start_date=row["start_date"],
            end_date=row["end_date"],
            initial_capital=row["initial_capital"],
            status=row["status"],
            progress=row["progress"],
            current_date=row["current_date"],
            total_assets=row["total_assets"],
            elapsed_seconds=row["elapsed_seconds"],
            error=row["error"],
            result=result,
            benchmark=row["benchmark"],
            request_json=row["request_json"],
            created_at=created_at,
            updated_at=updated_at,
        )


run_store = RunStore()
