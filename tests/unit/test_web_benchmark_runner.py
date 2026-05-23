from __future__ import annotations

import pandas as pd

from web.benchmark_runner import run_benchmark
from web.benchmark_store import BenchmarkRunStore


def test_run_benchmark_writes_experiment_package(tmp_path):
    price_csv = tmp_path / "prices.csv"
    _price_frame().to_csv(price_csv, index=False)
    store = BenchmarkRunStore(tmp_path / "benchmark.db")
    request = {
        "price_csv": str(price_csv),
        "output_dir": str(tmp_path / "benchmark_output"),
        "universe_id": "core50",
        "pit_csv": None,
        "start_date": "2024-01-01",
        "end_date": "2024-04-15",
        "lookback": 20,
        "top_n": 3,
        "rebalance": "weekly",
        "initial_capital": 1_000_000,
        "commission_rate": 0.00015,
        "stamp_tax_rate": 0.0005,
        "min_commission": 5,
        "max_position_weight": 1.0,
    }
    record = store.create(
        name="20日动量 TopN Benchmark",
        universe_id="core50",
        request=request,
        output_dir=request["output_dir"],
    )

    run_benchmark(record.run_id, request, store=store)

    saved = store.get(record.run_id)
    assert saved is not None
    assert saved.status == "completed", saved.error
    assert saved.result is not None
    assert saved.result["summary"]["summary"]["trading_days"] > 0
    assert saved.result["tables"]["equity_curve"]
    assert set(saved.artifacts) >= {"config", "summary", "report", "equity_curve", "trades"}


def _price_frame() -> pd.DataFrame:
    symbols = ["600519.SH", "000858.SZ", "600036.SH", "601318.SH", "600276.SH"]
    dates = pd.bdate_range("2024-01-01", periods=90)
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        for day_index, trade_date in enumerate(dates):
            drift = 1 + symbol_index * 0.004
            close = 100 + day_index * drift + symbol_index
            rows.append({
                "date": trade_date.date().isoformat(),
                "symbol": symbol,
                "open": close * 0.998,
                "close": close,
            })
    return pd.DataFrame(rows)
