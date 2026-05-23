from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from web.research_runner import compute_factor_from_prices, run_factor_research
from web.research_store import ResearchRunStore


def test_compute_factor_from_prices_generates_preset_factor_values():
    prices = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=25).repeat(2),
            "symbol": ["A", "B"] * 25,
            "close": [10 + i for i in range(25) for _ in range(2)],
        }
    )

    factor = compute_factor_from_prices(prices, "momentum_20d")

    valid = factor.dropna(subset=["factor"])
    assert len(valid) == 10
    assert valid["factor"].iloc[0] > 0


def test_run_factor_research_writes_result_and_artifacts(tmp_path):
    prices = _price_frame()
    price_csv = tmp_path / "prices.csv"
    prices.to_csv(price_csv, index=False)

    store = ResearchRunStore(tmp_path / "research.db")
    request = {
        "factor_id": "momentum_20d",
        "factor_params": {},
        "universe_id": "core50",
        "price_csv": str(price_csv),
        "pit_csv": None,
        "start_date": "2024-02-01",
        "end_date": "2024-03-15",
        "forward_periods": [1, 5],
        "groups": 3,
        "ic_method": "spearman",
        "rebalance": "weekly",
        "sample_split_date": "2024-02-20",
        "winsorize": True,
        "zscore": True,
        "neutralize": "none",
        "output_dir": str(tmp_path / "research_output"),
    }
    record = store.create(
        factor_id="momentum_20d",
        factor_name="20日动量",
        universe_id="core50",
        start_date=request["start_date"],
        end_date=request["end_date"],
        request=request,
        output_dir=request["output_dir"],
    )

    run_factor_research(record.run_id, request, store=store)

    saved = store.get(record.run_id)
    assert saved is not None
    assert saved.status == "completed", saved.error
    assert saved.result is not None
    assert saved.result["diagnostics"]["data_quality"] == "best_effort_static"
    assert saved.result["tables"]["ic_summary"]
    assert saved.result["tables"]["group_nav"]
    assert set(saved.artifacts) >= {"summary", "report", "coverage", "ic_summary"}
    summary = json.loads(Path(saved.artifacts["summary"]).read_text(encoding="utf-8"))
    assert summary["factor_name"] == "20日动量"


def _price_frame() -> pd.DataFrame:
    symbols = ["600519.SH", "000858.SZ", "600036.SH", "601318.SH", "600276.SH"]
    dates = pd.bdate_range("2024-01-01", periods=70)
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        for day_index, trade_date in enumerate(dates):
            drift = 1 + symbol_index * 0.003
            close = 100 + day_index * drift + symbol_index
            rows.append({"date": trade_date.date().isoformat(), "symbol": symbol, "close": close})
    return pd.DataFrame(rows)
