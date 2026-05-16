"""
SQLite 持久化层。

存储模拟盘/实盘会话、成交记录、每日净值快照。
数据库文件默认放在 data/live.db。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path("data/live.db")
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """每个线程一个连接（SQLite 不支持跨线程共享连接）。"""
    if not hasattr(_local, "conn"):
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn


def init_db() -> None:
    """建表（幂等）。"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            strategy_id  TEXT NOT NULL,
            symbols      TEXT NOT NULL,        -- JSON array
            mode         TEXT NOT NULL DEFAULT 'paper',
            status       TEXT NOT NULL DEFAULT 'starting',
            initial_capital REAL NOT NULL DEFAULT 1000000,
            total_assets REAL,
            cash         REAL,
            start_date   TEXT,
            end_date     TEXT,
            error        TEXT,
            started_at   TEXT NOT NULL,
            finished_at  TEXT,
            elapsed_seconds REAL NOT NULL DEFAULT 0,
            final_positions TEXT,                    -- JSON: 结束时持仓快照
            metrics_json    TEXT                     -- JSON: 绩效指标快照
        );

        CREATE TABLE IF NOT EXISTS trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL REFERENCES sessions(session_id),
            trade_id     TEXT NOT NULL,
            symbol       TEXT NOT NULL,
            side         TEXT NOT NULL,
            price        REAL NOT NULL,
            quantity     INTEGER NOT NULL,
            amount       REAL NOT NULL,
            commission   REAL NOT NULL DEFAULT 0,
            stamp_tax    REAL NOT NULL DEFAULT 0,
            net_amount   REAL NOT NULL,
            trade_date   TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL REFERENCES sessions(session_id),
            trade_date   TEXT NOT NULL,
            total_assets REAL NOT NULL,
            cash         REAL NOT NULL,
            position_value REAL NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
        CREATE INDEX IF NOT EXISTS idx_equity_session ON equity_snapshots(session_id);
    """)
    # 迁移：为已有数据库添加新列
    for col, ddl in [
        ("elapsed_seconds", "ALTER TABLE sessions ADD COLUMN elapsed_seconds REAL NOT NULL DEFAULT 0"),
        ("final_positions", "ALTER TABLE sessions ADD COLUMN final_positions TEXT"),
        ("metrics_json", "ALTER TABLE sessions ADD COLUMN metrics_json TEXT"),
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # 列已存在
    conn.commit()


# ── 会话 CRUD ─────────────────────────────────────────────────────────────────


def save_session(
    session_id: str,
    strategy_id: str,
    symbols: list[str],
    mode: str,
    initial_capital: float,
    start_date: str,
    end_date: str,
    started_at: datetime,
) -> None:
    import json
    conn = _get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO sessions
           (session_id, strategy_id, symbols, mode, initial_capital,
            start_date, end_date, started_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'starting')""",
        (session_id, strategy_id, json.dumps(symbols), mode,
         initial_capital, start_date, end_date, started_at.isoformat()),
    )
    conn.commit()


def update_session_status(
    session_id: str,
    status: str,
    total_assets: float | None = None,
    cash: float | None = None,
    error: str | None = None,
    elapsed_seconds: float = 0.0,
    positions_json: str | None = None,
    metrics_json: str | None = None,
) -> None:
    import json as _json
    conn = _get_conn()
    finished = datetime.now().isoformat() if status in ("stopped", "failed") else None
    conn.execute(
        """UPDATE sessions
           SET status=?, total_assets=?, cash=?, error=?, finished_at=COALESCE(?, finished_at),
               elapsed_seconds=MAX(elapsed_seconds, ?),
               final_positions=COALESCE(?, final_positions),
               metrics_json=COALESCE(?, metrics_json)
           WHERE session_id=?""",
        (status, total_assets, cash, error, finished, elapsed_seconds,
         positions_json, metrics_json, session_id),
    )
    conn.commit()


def get_session(session_id: str) -> Optional[dict]:
    import json
    conn = _get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["symbols"] = json.loads(d["symbols"])
    return d


def delete_session(session_id: str) -> bool:
    conn = _get_conn()
    conn.execute("DELETE FROM equity_snapshots WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM trades WHERE session_id=?", (session_id,))
    cur = conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
    conn.commit()
    return cur.rowcount > 0


def list_sessions(limit: int = 50) -> list[dict]:
    import json
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["symbols"] = json.loads(d["symbols"])
        result.append(d)
    return result


# ── 成交记录 ──────────────────────────────────────────────────────────────────


def save_trade(session_id: str, trade: dict) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO trades
           (session_id, trade_id, symbol, side, price, quantity,
            amount, commission, stamp_tax, net_amount, trade_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, trade["trade_id"], trade["symbol"], trade["side"],
         trade["price"], trade["quantity"], trade["amount"],
         trade["commission"], trade["stamp_tax"], trade["net_amount"],
         trade["trade_date"]),
    )
    conn.commit()


def get_trades(session_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE session_id=? ORDER BY trade_date, id", (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── 净值快照 ──────────────────────────────────────────────────────────────────


def save_equity_snapshot(
    session_id: str,
    trade_date: str,
    total_assets: float,
    cash: float,
    position_value: float,
) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO equity_snapshots
           (session_id, trade_date, total_assets, cash, position_value)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, trade_date, total_assets, cash, position_value),
    )
    conn.commit()


def get_equity_curve(session_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT trade_date, total_assets, cash, position_value
           FROM equity_snapshots WHERE session_id=?
           ORDER BY trade_date""",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# 启动时自动建表
init_db()
