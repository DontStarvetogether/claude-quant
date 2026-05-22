"""
回测执行器。

BacktestEngine.run() 是同步阻塞的，在线程池中执行，
通过 progress_callback 将进度写入 RunRecord（内存共享）。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from typing import Any

from loguru import logger

from cq.engine.backtest_engine import BacktestEngine
from cq.strategy.registry import get_strategy_list, load_strategy
from cq.utils.config import Config
from web.store import RunRecord, run_store

_executor = ThreadPoolExecutor(max_workers=4)


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
        benchmark,
        config_path,
    )


def _ensure_data(
    config: Config,
    symbols: list[str],
    start_date: str,
    end_date: str,
    record: RunRecord,
    start_time: float,
    benchmark: str | None = None,
) -> None:
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
            pipeline.update_symbol(sym, end, start_date=start)
        except Exception as e:
            logger.warning(f"数据下载失败 {sym}: {e}，将使用已有本地数据")

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
            pipeline.update_symbol(benchmark, end, start_date=start)
        except Exception as e:
            logger.warning(f"基准数据下载失败 {benchmark}: {e}，将使用已有本地数据")


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
        config.risk.max_position_pct = risk_params.get("max_position_pct", 0.20)
        config.risk.min_cash_reserve = risk_params.get("min_cash_reserve", 0.05)
        config.risk.max_drawdown_stop = risk_params.get("max_drawdown_stop", 0.15)

        # 从 AKShare 下载/增量更新数据
        _ensure_data(config, symbols, start_date, end_date, record, start_time, benchmark)

        strategy = load_strategy(strategy_id, strategy_params)

        engine = BacktestEngine(config, progress_callback=on_progress)
        engine.add_strategy(strategy, symbols=symbols)
        result = engine.run(start_date, end_date, benchmark=benchmark)

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
