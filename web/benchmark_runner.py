"""Web benchmark runner."""

from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from cq.benchmark import MomentumTopNConfig, export_benchmark_result, run_momentum_topn_benchmark
from cq.universe import PointInTimeUniverseProvider, filter_prices_by_pit_universe
from web.benchmark_store import BenchmarkRunStore, benchmark_store

_executor = ThreadPoolExecutor(max_workers=2)


def submit_benchmark(
    run_id: str,
    request: dict[str, Any],
    *,
    store: BenchmarkRunStore = benchmark_store,
) -> None:
    _executor.submit(run_benchmark, run_id, request, store=store)


def run_benchmark(
    run_id: str,
    request: dict[str, Any],
    *,
    store: BenchmarkRunStore = benchmark_store,
) -> None:
    started = time.perf_counter()

    def update(progress: int, step: str) -> None:
        store.update_status(
            run_id,
            status="running",
            progress=progress,
            current_step=step,
            elapsed_seconds=time.perf_counter() - started,
        )

    try:
        update(10, "加载价格数据")
        prices = _load_prices(Path(str(request["price_csv"])).expanduser())
        prices = _filter_dates(prices, request)

        update(28, "解析股票池")
        prices, universe_diagnostics = _filter_universe(prices, request)

        update(50, "运行 benchmark")
        cfg = MomentumTopNConfig(
            lookback=int(request.get("lookback") or 20),
            top_n=int(request.get("top_n") or 20),
            rebalance="D" if request.get("rebalance") == "daily" else "W",
            initial_capital=float(request.get("initial_capital") or 1_000_000),
            commission_rate=float(request.get("commission_rate") or 0.0),
            stamp_tax_rate=float(request.get("stamp_tax_rate") or 0.0),
            min_commission=float(request.get("min_commission") or 0.0),
            max_position_weight=float(request.get("max_position_weight") or 1.0),
        )
        result = run_momentum_topn_benchmark(prices, cfg)

        update(82, "导出实验包")
        output_dir = _output_dir(run_id, request.get("output_dir"))
        config = _config_payload(request, cfg)
        exported = export_benchmark_result(
            result,
            output_dir,
            universe=request.get("universe_id"),
            metadata={"universe_diagnostics": universe_diagnostics},
            config=config,
        )
        tables = {
            "equity_curve": _records(result.equity_curve),
            "holdings": _records(result.holdings),
            "trades": _records(result.trades),
            "signals": _records(result.signals),
        }
        summary = _read_summary(exported.files["summary"])
        diagnostics = {
            "universe": universe_diagnostics,
            "table_rows": {key: len(value) for key, value in tables.items()},
            "output_dir": str(exported.output_dir),
        }
        store.save_result(
            run_id,
            result={"summary": summary, "diagnostics": diagnostics, "tables": tables},
            artifacts={key: str(path) for key, path in exported.files.items()},
            output_dir=str(exported.output_dir),
            elapsed_seconds=time.perf_counter() - started,
        )
    except Exception as exc:
        store.save_error(run_id, str(exc), time.perf_counter() - started)


def _load_prices(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"价格数据 CSV 不存在: {path}")
    prices = pd.read_csv(path)
    required = ["date", "symbol", "open", "close"]
    missing = [column for column in required if column not in prices.columns]
    if missing:
        raise ValueError(f"prices missing required columns: {', '.join(missing)}")
    prices = prices[required].copy()
    prices["date"] = pd.to_datetime(prices["date"])
    prices["symbol"] = prices["symbol"].astype(str).str.upper()
    prices["open"] = pd.to_numeric(prices["open"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=required)
    return prices.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)


def _filter_dates(prices: pd.DataFrame, request: dict[str, Any]) -> pd.DataFrame:
    data = prices
    if request.get("start_date"):
        data = data[data["date"] >= pd.Timestamp(request["start_date"])]
    if request.get("end_date"):
        data = data[data["date"] <= pd.Timestamp(request["end_date"])]
    if data.empty:
        raise ValueError("指定区间内没有 benchmark 价格样本")
    return data.reset_index(drop=True)


def _filter_universe(
    prices: pd.DataFrame,
    request: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pit_csv = request.get("pit_csv")
    universe_id = request.get("universe_id")
    if pit_csv and universe_id:
        provider = PointInTimeUniverseProvider.from_csv(str(pit_csv))
        filtered, diagnostics = filter_prices_by_pit_universe(prices, provider, str(universe_id))
        diagnostics["source"] = "pit_csv"
        diagnostics["quality"] = "strict_historical_pit"
        if filtered.empty:
            raise ValueError(f"PIT 股票池 {universe_id} 在 benchmark 区间没有样本")
        return filtered, diagnostics
    return prices, {
        "universe_id": universe_id,
        "source": "price_csv",
        "quality": "price_csv_scope",
        "input_rows": int(len(prices)),
        "output_rows": int(len(prices)),
    }


def _output_dir(run_id: str, requested: Any) -> Path:
    root = Path(str(requested)).expanduser() if requested else Path("output/benchmark")
    return root / run_id


def _config_payload(request: dict[str, Any], cfg: MomentumTopNConfig) -> dict[str, Any]:
    return {
        "price_csv": request.get("price_csv"),
        "universe_id": request.get("universe_id"),
        "pit_csv": request.get("pit_csv"),
        "start_date": request.get("start_date"),
        "end_date": request.get("end_date"),
        "lookback": cfg.lookback,
        "top_n": cfg.top_n,
        "rebalance": cfg.rebalance,
        "initial_capital": cfg.initial_capital,
        "commission_rate": cfg.commission_rate,
        "stamp_tax_rate": cfg.stamp_tax_rate,
        "min_commission": cfg.min_commission,
        "max_position_weight": cfg.max_position_weight,
    }


def _records(frame: pd.DataFrame, *, limit: int = 5000) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [_json_safe(row) for row in frame.head(limit).to_dict("records")]


def _read_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
