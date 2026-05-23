"""Async factor research runner used by the Web API."""

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

from cq.research import (
    analyze_factor_groups,
    calculate_forward_returns,
    calculate_ic,
    export_factor_report,
    summarize_ic,
)
from cq.universe import (
    PointInTimeUniverseProvider,
    UniverseNotFoundError,
    filter_prices_by_pit_universe,
    get_builtin_universe_provider,
)
from web.research_store import ResearchRunStore, research_store

_executor = ThreadPoolExecutor(max_workers=2)

DATE_COL = "date"
SYMBOL_COL = "symbol"
PRICE_COL = "close"
FACTOR_COL = "factor"

FACTOR_PRESETS: dict[str, dict[str, Any]] = {
    "momentum_20d": {
        "id": "momentum_20d",
        "name": "20日动量",
        "description": "过去 20 个交易日涨跌幅，数值越高代表近期趋势越强。",
        "params": {"lookback": 20},
    },
    "reversal_5d": {
        "id": "reversal_5d",
        "name": "5日反转",
        "description": "过去 5 个交易日涨跌幅取反，数值越高代表近期回落越多。",
        "params": {"lookback": 5},
    },
    "volatility_20d": {
        "id": "volatility_20d",
        "name": "20日波动率",
        "description": "过去 20 个交易日收益率标准差，数值越高代表短期波动越大。",
        "params": {"window": 20},
    },
    "ma_trend_20_60": {
        "id": "ma_trend_20_60",
        "name": "20/60均线趋势",
        "description": "20 日均线相对 60 日均线的偏离，数值越高代表中期趋势越强。",
        "params": {"fast": 20, "slow": 60},
    },
}


def list_factor_presets() -> list[dict[str, Any]]:
    """Return Web-facing factor presets."""

    return [dict(item) for item in FACTOR_PRESETS.values()]


def factor_name(factor_id: str) -> str:
    try:
        return str(FACTOR_PRESETS[factor_id]["name"])
    except KeyError as exc:
        raise ValueError(f"未知因子: {factor_id}") from exc


def submit_factor_research(
    run_id: str,
    request: dict[str, Any],
    *,
    store: ResearchRunStore = research_store,
) -> None:
    """Submit a factor research job to the background executor."""

    _executor.submit(run_factor_research, run_id, request, store=store)


def run_factor_research(
    run_id: str,
    request: dict[str, Any],
    *,
    store: ResearchRunStore = research_store,
) -> None:
    """Run a complete factor research job and persist result metadata."""

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
        update(5, "加载行情")
        price_csv = _require_price_csv(request)
        prices = _load_prices(price_csv)
        date_bounds = _date_bounds(request)

        update(18, "计算因子")
        factor = compute_factor_from_prices(
            prices,
            str(request["factor_id"]),
            dict(request.get("factor_params") or {}),
        )
        factor = _filter_date_range(factor, *date_bounds)

        update(32, "加载股票池")
        factor, universe_diagnostics = _filter_factor_universe(factor, request)
        factor = _apply_cross_section_transforms(
            factor,
            winsorize=bool(request.get("winsorize", True)),
            zscore=bool(request.get("zscore", True)),
        )

        update(48, "计算未来收益")
        periods = _normalize_periods(request.get("forward_periods") or [1, 5, 20])
        forward_returns = calculate_forward_returns(prices, periods=periods)

        update(64, "计算 IC")
        analysis = analyze_factor_groups(
            factor,
            forward_returns,
            group_count=int(request.get("groups") or 5),
            periods=periods,
            date_col=DATE_COL,
            symbol_col=SYMBOL_COL,
            factor_col=FACTOR_COL,
        )
        factor_return = factor[[DATE_COL, SYMBOL_COL, FACTOR_COL]].merge(
            forward_returns,
            on=[DATE_COL, SYMBOL_COL],
            how="left",
        )
        ic = calculate_ic(
            factor_return,
            periods=periods,
            date_col=DATE_COL,
            factor_col=FACTOR_COL,
            method=str(request.get("ic_method") or "spearman"),
        )
        ic_summary = summarize_ic(ic)

        update(82, "生成报告")
        output_dir = _output_dir(run_id, request.get("output_dir"))
        metadata = _metadata(request, universe_diagnostics, price_csv)
        exported = export_factor_report(
            factor_name=factor_name(str(request["factor_id"])),
            analysis=analysis,
            ic_summary=ic_summary,
            output_dir=output_dir,
            universe=str(request.get("universe_id") or ""),
            start_date=str(request.get("start_date") or ""),
            end_date=str(request.get("end_date") or ""),
            metadata=metadata,
            sample_split_date=request.get("sample_split_date"),
            date_col=DATE_COL,
        )

        tables = _tables(analysis, ic, ic_summary)
        summary = _read_summary(exported.files["summary"])
        diagnostics = _diagnostics(
            request=request,
            prices=prices,
            factor=factor,
            periods=periods,
            universe_diagnostics=universe_diagnostics,
            summary=summary,
            tables=tables,
        )
        result = {
            "summary": summary,
            "diagnostics": diagnostics,
            "tables": tables,
        }
        artifacts = {key: str(path) for key, path in exported.files.items()}
        store.save_result(
            run_id,
            result=result,
            artifacts=artifacts,
            output_dir=str(exported.output_dir),
            elapsed_seconds=time.perf_counter() - started,
        )
    except Exception as exc:
        store.save_error(run_id, str(exc), time.perf_counter() - started)


def compute_factor_from_prices(
    prices: pd.DataFrame,
    factor_id: str,
    factor_params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Build preset factor values from long-form price data."""

    if factor_id not in FACTOR_PRESETS:
        raise ValueError(f"未知因子: {factor_id}")
    _require_columns(prices, [DATE_COL, SYMBOL_COL, PRICE_COL])
    params = {**FACTOR_PRESETS[factor_id]["params"], **(factor_params or {})}

    frame = prices[[DATE_COL, SYMBOL_COL, PRICE_COL]].copy()
    frame[DATE_COL] = pd.to_datetime(frame[DATE_COL])
    frame[SYMBOL_COL] = frame[SYMBOL_COL].astype(str).str.upper()
    frame[PRICE_COL] = pd.to_numeric(frame[PRICE_COL], errors="coerce")
    frame = frame.sort_values([SYMBOL_COL, DATE_COL], kind="mergesort").reset_index(drop=True)
    close = frame[PRICE_COL]
    grouped_close = close.groupby(frame[SYMBOL_COL], sort=False)

    if factor_id == "momentum_20d":
        lookback = int(params.get("lookback", 20))
        values = close / grouped_close.shift(lookback) - 1.0
    elif factor_id == "reversal_5d":
        lookback = int(params.get("lookback", 5))
        values = -(close / grouped_close.shift(lookback) - 1.0)
    elif factor_id == "volatility_20d":
        window = int(params.get("window", 20))
        returns = grouped_close.pct_change()
        values = returns.groupby(frame[SYMBOL_COL], sort=False).rolling(window).std().reset_index(
            level=0,
            drop=True,
        )
    elif factor_id == "ma_trend_20_60":
        fast = int(params.get("fast", 20))
        slow = int(params.get("slow", 60))
        fast_ma = grouped_close.rolling(fast).mean().reset_index(level=0, drop=True)
        slow_ma = grouped_close.rolling(slow).mean().reset_index(level=0, drop=True)
        values = fast_ma / slow_ma - 1.0
    else:  # pragma: no cover - guarded by preset validation
        raise ValueError(f"未实现因子: {factor_id}")

    out = frame[[DATE_COL, SYMBOL_COL]].copy()
    out[FACTOR_COL] = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return out


def _require_price_csv(request: dict[str, Any]) -> Path:
    price_csv = request.get("price_csv")
    if not price_csv:
        raise ValueError("price_csv is required")
    path = Path(str(price_csv)).expanduser()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"价格数据 CSV 不存在: {path}")
    return path


def _load_prices(path: Path) -> pd.DataFrame:
    prices = pd.read_csv(path)
    _require_columns(prices, [DATE_COL, SYMBOL_COL, PRICE_COL])
    prices = prices.copy()
    prices[DATE_COL] = pd.to_datetime(prices[DATE_COL])
    prices[SYMBOL_COL] = prices[SYMBOL_COL].astype(str).str.upper()
    prices[PRICE_COL] = pd.to_numeric(prices[PRICE_COL], errors="coerce")
    prices = prices.dropna(subset=[DATE_COL, SYMBOL_COL, PRICE_COL])
    return prices.sort_values([SYMBOL_COL, DATE_COL], kind="mergesort").reset_index(drop=True)


def _date_bounds(request: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(request["start_date"])
    end = pd.Timestamp(request["end_date"])
    if start > end:
        raise ValueError("start_date must be earlier than or equal to end_date")
    return start, end


def _filter_date_range(
    factor: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    data = factor[(factor[DATE_COL] >= start) & (factor[DATE_COL] <= end)].copy()
    if data.empty:
        raise ValueError("指定区间内没有因子样本")
    return data.reset_index(drop=True)


def _filter_factor_universe(
    factor: pd.DataFrame,
    request: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    universe_id = str(request.get("universe_id") or "")
    pit_csv = request.get("pit_csv")
    if pit_csv:
        provider = PointInTimeUniverseProvider.from_csv(str(pit_csv))
        filtered, diagnostics = filter_prices_by_pit_universe(
            factor,
            provider,
            universe_id,
            date_col=DATE_COL,
            symbol_col=SYMBOL_COL,
        )
        diagnostics["source"] = "pit_csv"
        diagnostics["quality"] = "strict_historical_pit"
        if filtered.empty:
            raise ValueError(f"PIT 股票池 {universe_id} 在测试区间没有样本")
        return filtered, diagnostics

    try:
        provider = get_builtin_universe_provider()
        symbols = set(provider.get_symbols(universe_id))
    except UniverseNotFoundError:
        symbols = set()

    if symbols:
        filtered = factor[factor[SYMBOL_COL].isin(symbols)].copy().reset_index(drop=True)
        diagnostics = {
            "universe_id": universe_id,
            "source": "builtin_static",
            "quality": "best_effort_static",
            "member_count": len(symbols),
            "input_rows": int(len(factor)),
            "output_rows": int(len(filtered)),
        }
        if filtered.empty:
            raise ValueError(f"静态股票池 {universe_id} 在测试区间没有样本")
        return filtered, diagnostics

    return factor, {
        "universe_id": universe_id,
        "source": "price_csv_all_symbols",
        "quality": "best_effort",
        "input_rows": int(len(factor)),
        "output_rows": int(len(factor)),
    }


def _apply_cross_section_transforms(
    factor: pd.DataFrame,
    *,
    winsorize: bool,
    zscore: bool,
) -> pd.DataFrame:
    data = factor.copy()
    data[FACTOR_COL] = pd.to_numeric(data[FACTOR_COL], errors="coerce")

    def transform(values: pd.Series) -> pd.Series:
        if winsorize and values.notna().sum() >= 5:
            lower = values.quantile(0.01)
            upper = values.quantile(0.99)
            values = values.clip(lower, upper)
        if zscore:
            std = values.std(ddof=0)
            mean = values.mean()
            if std and math.isfinite(std):
                values = (values - mean) / std
        return values

    data[FACTOR_COL] = data.groupby(DATE_COL, sort=True)[FACTOR_COL].transform(transform)
    return data.reset_index(drop=True)


def _normalize_periods(raw_periods: list[Any]) -> list[int]:
    periods: list[int] = []
    for raw in raw_periods:
        period = int(raw)
        if period <= 0:
            raise ValueError("forward_periods must contain positive integers")
        if period not in periods:
            periods.append(period)
    if not periods:
        raise ValueError("forward_periods must not be empty")
    return periods


def _output_dir(run_id: str, requested: Any) -> Path:
    root = Path(str(requested)).expanduser() if requested else Path("output/research")
    return root / run_id


def _metadata(
    request: dict[str, Any],
    universe_diagnostics: dict[str, Any],
    price_csv: Path,
) -> dict[str, Any]:
    neutralize = str(request.get("neutralize") or "none")
    return {
        "factor_id": request.get("factor_id"),
        "rebalance": request.get("rebalance"),
        "ic_method": request.get("ic_method"),
        "groups": request.get("groups"),
        "forward_periods": ",".join(str(p) for p in request.get("forward_periods", [])),
        "winsorize": bool(request.get("winsorize", True)),
        "zscore": bool(request.get("zscore", True)),
        "neutralize": neutralize,
        "neutralize_status": "applied" if neutralize == "none" else "unavailable_no_metadata",
        "price_csv": str(price_csv),
        "universe_quality": universe_diagnostics.get("quality"),
        "universe_source": universe_diagnostics.get("source"),
    }


def _tables(
    analysis: Any,
    ic: pd.DataFrame,
    ic_summary: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "coverage": _records(analysis.coverage),
        "ic": _records(ic),
        "ic_summary": _records(ic_summary),
        "group_return": _records(analysis.group_return),
        "group_nav": _records(analysis.group_nav),
        "top_bottom_return": _records(analysis.top_bottom_return),
        "monotonicity": _records(analysis.monotonicity),
        "turnover_by_group": _records(analysis.turnover_by_group),
    }


def _records(frame: pd.DataFrame, *, limit: int = 5000) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clipped = frame.head(limit).copy()
    return [_json_safe(row) for row in clipped.to_dict("records")]


def _read_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _diagnostics(
    *,
    request: dict[str, Any],
    prices: pd.DataFrame,
    factor: pd.DataFrame,
    periods: list[int],
    universe_diagnostics: dict[str, Any],
    summary: dict[str, Any],
    tables: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    valid_factor = int(factor[FACTOR_COL].notna().sum())
    return {
        "price_rows": int(len(prices)),
        "factor_rows": int(len(factor)),
        "valid_factor_rows": valid_factor,
        "missing_factor_rows": int(len(factor) - valid_factor),
        "forward_periods": periods,
        "data_quality": universe_diagnostics.get("quality", "unknown"),
        "universe": universe_diagnostics,
        "lookahead_risk": "checked_forward_returns_shifted_by_symbol",
        "sample_diagnostics": summary.get("sample_diagnostics", {}),
        "neutralize_status": (
            "applied" if request.get("neutralize") == "none" else "unavailable_no_metadata"
        ),
        "table_rows": {name: len(rows) for name, rows in tables.items()},
    }


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


def _require_columns(frame: pd.DataFrame, columns: list[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
