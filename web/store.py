"""内存运行记录存储"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from cq.engine.backtest_engine import BacktestResult


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
    created_at: datetime = field(default_factory=datetime.now)


class RunStore:
    """单例内存存储，保存本次进程内的所有回测运行记录。"""

    _instance: Optional["RunStore"] = None

    def __new__(cls) -> "RunStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._records: dict[str, RunRecord] = {}
        return cls._instance

    def create(
        self,
        strategy_name: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float,
    ) -> RunRecord:
        run_id = str(uuid.uuid4())
        record = RunRecord(
            run_id=run_id,
            strategy_name=strategy_name,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
        )
        self._records[run_id] = record
        return record

    def get(self, run_id: str) -> Optional[RunRecord]:
        return self._records.get(run_id)

    def all(self) -> list[RunRecord]:
        return sorted(
            self._records.values(),
            key=lambda r: r.created_at,
            reverse=True,
        )

    def delete(self, run_id: str) -> bool:
        if run_id in self._records:
            del self._records[run_id]
            return True
        return False


# 全局单例
run_store = RunStore()
