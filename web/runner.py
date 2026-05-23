"""
回测执行器。

BacktestEngine.run() 是同步阻塞的，在线程池中执行，
通过 progress_callback 将进度写入 RunRecord（内存共享）。
"""

from __future__ import annotations

import time
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from typing import Any

from loguru import logger

from cq.engine.backtest_engine import BacktestEngine
from cq.strategy.registry import get_strategy_list, load_strategy
from cq.utils.config import Config
from web.store import RunRecord, run_store

_executor = ThreadPoolExecutor(max_workers=4)


def _diagnostic_summary(
    symbols: list[dict],
    benchmark: dict | None = None,
) -> dict:
    items = symbols + ([benchmark] if benchmark else [])
    total = len(items)
    updated = sum(1 for item in items if item.get("status") == "updated")
    cache_hit = sum(1 for item in items if item.get("status") == "cache_hit")
    failed = sum(1 for item in items if str(item.get("status", "")).startswith("download_failed"))
    missing = sum(1 for item in items if item.get("status") in ("download_failed_no_cache", "empty_source"))
    return {
        "total": total,
        "updated": updated,
        "cache_hit": cache_hit,
        "failed": failed,
        "missing": missing,
    }


def _build_universe_diagnostics(
    strategy_id: str,
    symbols: list[str],
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """记录股票池构造方式，显式暴露静态股票池的幸存者偏差风险。"""
    symbol_count = len(set(symbols))
    requested_universe = (request or {}).get("universe") if request else None
    universe_id = requested_universe or "custom_static"
    universe_name = "自定义静态股票池"
    source = "user_selection"
    warnings: list[str] = []
    notes: list[str] = []

    if requested_universe:
        universe_name = str(requested_universe)
        source = "request"

    if strategy_id == "trend_rank" and symbol_count >= 20:
        risk = "high"
        warnings.append("static_universe_survivorship_bias")
        notes.append("组合选股策略使用静态股票池回测历史，可能明显高估历史可投资机会。")
    elif symbol_count >= 10:
        risk = "medium"
        warnings.append("static_universe_survivorship_bias")
        notes.append("多标的静态股票池未记录历史成分，存在幸存者偏差风险。")
    else:
        risk = "low"
        notes.append("少量自选标的回测仍是静态样本，不能代表当时完整可投资范围。")

    return {
        "universe_id": universe_id,
        "universe_name": universe_name,
        "source": source,
        "construction": "static",
        "selection_time": "run_submit",
        "symbol_count": symbol_count,
        "survivorship_bias_risk": risk,
        "history_membership_available": False,
        "point_in_time": False,
        "warnings": warnings,
        "notes": notes,
    }


def submit_backtest(
    run_id: str,
    strategy_id: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
    slippage: float = 0.0,
    adjust: str = "dynamic",
    enable_capacity_limit: bool = True,
    max_volume_participation: float = 0.10,
    benchmark: str | None = None,
    config_path: str = "config/local.yaml",
) -> None:
    """提交回测任务到线程池（非阻塞）。"""
    _executor.submit(
        _run_backtest,
        run_id,
        strategy_id,
        symbols,
        start_date,
        end_date,
        initial_capital,
        strategy_params,
        risk_params,
        slippage,
        adjust,
        enable_capacity_limit,
        max_volume_participation,
        benchmark,
        config_path,
    )


def _validate_trade_symbol_data(
    diagnostics: list[dict],
    start_date: str,
    end_date: str,
) -> None:
    """交易标的数据不足会直接阻断回测，避免生成貌似成功但不可用的结果。"""
    blocking: list[str] = []
    for item in diagnostics:
        symbol = item.get("symbol", "UNKNOWN")
        status = item.get("status")
        quality = item.get("data_quality") or {}
        warnings = set(quality.get("warnings") or [])
        coverage_status = quality.get("coverage_status")

        if status in ("download_failed_no_cache", "empty_source"):
            blocking.append(f"{symbol}: 无可用数据（{status}）")
        elif not item.get("used_cache"):
            blocking.append(f"{symbol}: 无本地缓存")
        elif not quality.get("qfq_available", True):
            blocking.append(f"{symbol}: 缺少 qfq 复权数据")
        elif coverage_status == "start_missing":
            blocking.append(f"{symbol}: 数据未覆盖开始日 {start_date}")
        elif coverage_status == "end_missing":
            blocking.append(f"{symbol}: 数据未覆盖结束日 {end_date}")
        elif coverage_status == "missing":
            blocking.append(f"{symbol}: 数据覆盖缺失")
        elif "qfq_missing" in warnings:
            blocking.append(f"{symbol}: 缺少 qfq 复权数据")
        elif "qfq_adjust_factor_missing" in warnings:
            blocking.append(f"{symbol}: qfq 缺少复权因子字段")
        elif "qfq_price_scale_mismatch" in warnings:
            blocking.append(f"{symbol}: qfq 涨跌停/昨收价格尺度异常")

    if blocking:
        detail = "；".join(blocking[:8])
        if len(blocking) > 8:
            detail += f"；等 {len(blocking)} 个问题"
        raise RuntimeError(f"交易标的数据质量校验失败：{detail}")


def _ensure_data(
    config: Config,
    symbols: list[str],
    start_date: str,
    end_date: str,
    record: RunRecord,
    start_time: float,
    benchmark: str | None = None,
) -> dict:
    """通过 DataPipeline 确保回测所需数据已下载到本地缓存。"""
    from cq.data.calendar import TradingCalendar
    from cq.data.pipeline import DataPipeline
    from cq.data.source import create_source
    from cq.data.store.parquet_store import ParquetStore

    store = ParquetStore(config.data.root_path)
    source = create_source(config.data.source)

    # 同步交易日历（本地没有时自动下载）
    calendar_days = store.read_calendar("SSE")
    if not calendar_days:
        elapsed = time.monotonic() - start_time
        record.current_date = "同步交易日历..."
        record.elapsed_seconds = elapsed
        run_store.update_status(
            record.run_id,
            current_date=record.current_date,
            elapsed_seconds=elapsed,
        )
        tmp_pipeline = DataPipeline(source, store, None)  # type: ignore[arg-type]
        tmp_pipeline.sync_calendar("SSE")
        calendar_days = store.read_calendar("SSE")

    if not calendar_days:
        raise RuntimeError("无法获取交易日历，请检查网络连接")

    calendar = TradingCalendar(calendar_days)
    pipeline = DataPipeline(source, store, calendar)

    end = _date.fromisoformat(end_date)
    start = _date.fromisoformat(start_date)

    symbol_diagnostics: list[dict] = []
    benchmark_diagnostic: dict | None = None

    for i, sym in enumerate(symbols):
        elapsed = time.monotonic() - start_time
        record.current_date = f"下载数据 {sym} ({i + 1}/{len(symbols)})..."
        record.elapsed_seconds = elapsed
        run_store.update_status(
            record.run_id,
            current_date=record.current_date,
            elapsed_seconds=elapsed,
        )
        try:
            diagnostic = pipeline.update_symbol_diagnostic(sym, end, start_date=start).to_dict()
        except Exception as e:
            logger.warning(f"数据下载失败 {sym}: {e}，将使用已有本地数据")
            local_min, local_max = store.get_available_dates(sym, adjust="raw")
            qfq_min, qfq_max = store.get_available_dates(sym, adjust="qfq")
            coverage_status = (
                "ok"
                if local_min and local_max and local_min <= start and local_max >= end
                else "start_missing" if local_min and local_min > start
                else "end_missing" if local_max and local_max < end else "missing"
            )
            diagnostic = {
                "symbol": sym,
                "role": "trade_symbol",
                "status": "download_failed_cache_available" if local_max else "download_failed_no_cache",
                "new_records": 0,
                "used_cache": local_max is not None,
                "local_first_date": str(local_min) if local_min else None,
                "local_last_date": str(local_max) if local_max else None,
                "requested_start": start_date,
                "requested_end": end_date,
                "error": str(e),
                "coverage_status": coverage_status,
                "data_quality": {
                    "status": "ok" if coverage_status == "ok" and qfq_max else "degraded",
                    "coverage_status": coverage_status,
                    "warnings": [] if coverage_status == "ok" and qfq_max else ["coverage_incomplete"],
                    "qfq_available": qfq_max is not None,
                    "factor_available": False,
                    "local_first_date": str(local_min) if local_min else None,
                    "local_last_date": str(local_max) if local_max else None,
                },
            }
        diagnostic["role"] = "trade_symbol"
        symbol_diagnostics.append(diagnostic)

    if benchmark:
        elapsed = time.monotonic() - start_time
        record.current_date = f"下载基准 {benchmark}..."
        record.elapsed_seconds = elapsed
        run_store.update_status(
            record.run_id,
            current_date=record.current_date,
            elapsed_seconds=elapsed,
        )
        try:
            benchmark_diagnostic = pipeline.update_symbol_diagnostic(
                benchmark, end, start_date=start
            ).to_dict()
        except Exception as e:
            logger.warning(f"基准数据下载失败 {benchmark}: {e}，将使用已有本地数据")
            local_min, local_max = store.get_available_dates(benchmark, adjust="raw")
            qfq_min, qfq_max = store.get_available_dates(benchmark, adjust="qfq")
            coverage_status = (
                "ok"
                if local_min and local_max and local_min <= start and local_max >= end
                else "start_missing" if local_min and local_min > start
                else "end_missing" if local_max and local_max < end else "missing"
            )
            benchmark_diagnostic = {
                "symbol": benchmark,
                "role": "benchmark",
                "status": "download_failed_cache_available" if local_max else "download_failed_no_cache",
                "new_records": 0,
                "used_cache": local_max is not None,
                "local_first_date": str(local_min) if local_min else None,
                "local_last_date": str(local_max) if local_max else None,
                "requested_start": start_date,
                "requested_end": end_date,
                "error": str(e),
                "coverage_status": coverage_status,
                "data_quality": {
                    "status": "ok" if coverage_status == "ok" and qfq_max else "degraded",
                    "coverage_status": coverage_status,
                    "warnings": [] if coverage_status == "ok" and qfq_max else ["coverage_incomplete"],
                    "qfq_available": qfq_max is not None,
                    "factor_available": False,
                    "local_first_date": str(local_min) if local_min else None,
                    "local_last_date": str(local_max) if local_max else None,
                },
            }
        benchmark_diagnostic["role"] = "benchmark"

    return {
        "symbols": symbol_diagnostics,
        "benchmark": benchmark_diagnostic,
        "summary": _diagnostic_summary(symbol_diagnostics, benchmark_diagnostic),
    }


def _run_backtest(
    run_id: str,
    strategy_id: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
    slippage: float,
    adjust: str,
    enable_capacity_limit: bool,
    max_volume_participation: float,
    benchmark: str | None,
    config_path: str,
) -> None:
    """在线程池中执行回测（同步阻塞）。"""
    record = run_store.get(run_id)
    if record is None:
        return

    start_time = time.monotonic()
    record.status = "running"
    record.total_assets = initial_capital  # 下载阶段显示初始资金
    run_store.update_status(
        run_id,
        status="running",
        total_assets=initial_capital,
        elapsed_seconds=0.0,
    )

    def on_progress(current: int, total: int, trade_date: _date, total_assets: float) -> None:
        record.progress = int(current / total * 100)
        record.current_date = str(trade_date)
        record.total_assets = total_assets
        record.elapsed_seconds = time.monotonic() - start_time
        run_store.update_status(
            run_id,
            progress=record.progress,
            current_date=record.current_date,
            total_assets=record.total_assets,
            elapsed_seconds=record.elapsed_seconds,
        )

    try:
        config = Config.from_yaml(config_path)
        config.engine.initial_capital = initial_capital
        config.engine.slippage = slippage
        config.engine.adjust = adjust
        config.engine.enable_capacity_limit = enable_capacity_limit
        config.engine.max_volume_participation = max_volume_participation
        config.risk.max_position_pct = risk_params.get("max_position_pct", 0.20)
        config.risk.min_cash_reserve = risk_params.get("min_cash_reserve", 0.05)
        config.risk.max_drawdown_stop = risk_params.get("max_drawdown_stop", 0.15)

        # 从 AKShare 下载/增量更新数据
        data_diagnostics = _ensure_data(
            config, symbols, start_date, end_date, record, start_time, benchmark
        )
        _validate_trade_symbol_data(
            data_diagnostics.get("symbols", []),
            start_date,
            end_date,
        )

        strategy = load_strategy(strategy_id, strategy_params)

        engine = BacktestEngine(config, progress_callback=on_progress)
        engine.add_strategy(strategy, symbols=symbols)
        request_snapshot = json.loads(record.request_json) if record.request_json else None
        universe_diagnostics = _build_universe_diagnostics(
            strategy_id,
            symbols,
            request=request_snapshot,
        )
        result = engine.run(
            start_date,
            end_date,
            benchmark=benchmark,
            data_diagnostics=data_diagnostics,
            universe_diagnostics=universe_diagnostics,
        )

        elapsed = time.monotonic() - start_time
        record.result = result
        record.status = "completed"
        record.progress = 100
        record.elapsed_seconds = elapsed
        run_store.save_result(run_id, result, elapsed)
        logger.info(f"回测完成 run_id={run_id}")

    except Exception as e:
        elapsed = time.monotonic() - start_time
        record.status = "failed"
        record.error = str(e)
        record.elapsed_seconds = elapsed
        run_store.save_error(run_id, str(e), elapsed)
        logger.error(f"回测失败 run_id={run_id}: {e}")
