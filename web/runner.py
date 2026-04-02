"""
回测执行器。

BacktestEngine.run() 是同步阻塞的，在线程池中执行，
通过 progress_callback 将进度写入 RunRecord（内存共享）。
"""

from __future__ import annotations

import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from typing import Any

from loguru import logger

from cq.engine.backtest_engine import BacktestEngine
from cq.utils.config import Config
from web.store import RunRecord, run_store

# 内置策略注册表：strategy_id → Python 类路径
BUILTIN_STRATEGIES: dict[str, str] = {
    "double_ma": "cq.strategy.examples.double_ma.DoubleMaStrategy",
    "rsi":       "cq.strategy.examples.rsi.RsiStrategy",
    "bollinger": "cq.strategy.examples.bollinger.BollingerStrategy",
    "momentum":  "cq.strategy.examples.momentum.MomentumStrategy",
}

# 策略元数据（名称、描述、参数定义）
STRATEGY_METADATA: dict[str, dict] = {
    "double_ma": {
        "name": "双均线策略",
        "description": "快线上穿慢线买入（金叉），下穿卖出（死叉）",
        "params": [
            {"name": "fast", "type": "int", "default": 5, "label": "快线周期", "min": 2, "max": 50, "step": 1},
            {"name": "slow", "type": "int", "default": 20, "label": "慢线周期", "min": 5, "max": 200, "step": 1},
        ],
    },
    "rsi": {
        "name": "RSI 策略",
        "description": "RSI 超卖时买入，超买时卖出（均值回归）",
        "params": [
            {"name": "period",       "type": "int",   "default": 14,  "label": "RSI 周期",   "min": 5,  "max": 60,  "step": 1},
            {"name": "oversold",     "type": "float", "default": 30,  "label": "超卖阈值",   "min": 10, "max": 40,  "step": 1},
            {"name": "overbought",   "type": "float", "default": 70,  "label": "超买阈值",   "min": 60, "max": 90,  "step": 1},
            {"name": "position_pct", "type": "float", "default": 0.9, "label": "仓位比例",   "min": 0.1,"max": 1.0, "step": 0.1},
        ],
    },
    "bollinger": {
        "name": "布林带策略",
        "description": "价格触及下轨买入，触及上轨卖出（均值回归）",
        "params": [
            {"name": "period",       "type": "int",   "default": 20,  "label": "均线周期",   "min": 5,  "max": 60,  "step": 1},
            {"name": "std_dev",      "type": "float", "default": 2.0, "label": "标准差倍数", "min": 1.0,"max": 3.0, "step": 0.5},
            {"name": "position_pct", "type": "float", "default": 0.9, "label": "仓位比例",   "min": 0.1,"max": 1.0, "step": 0.1},
        ],
    },
    "momentum": {
        "name": "动量策略",
        "description": "N 日涨幅超阈值买入，跌破阈值卖出（趋势跟随）",
        "params": [
            {"name": "lookback",        "type": "int",   "default": 20,   "label": "回看天数",   "min": 5,   "max": 60,  "step": 1},
            {"name": "buy_threshold",   "type": "float", "default": 0.05, "label": "买入阈值",   "min": 0.01,"max": 0.3, "step": 0.01},
            {"name": "sell_threshold",  "type": "float", "default": -0.05,"label": "卖出阈值",   "min": -0.3,"max": -0.01,"step": 0.01},
            {"name": "position_pct",    "type": "float", "default": 0.9,  "label": "仓位比例",   "min": 0.1, "max": 1.0, "step": 0.1},
        ],
    },
}

_executor = ThreadPoolExecutor(max_workers=4)


def get_strategy_list() -> list[dict]:
    return [
        {"id": sid, **meta}
        for sid, meta in STRATEGY_METADATA.items()
    ]


def load_strategy(strategy_id: str, params: dict[str, Any]):
    """加载策略类并设置参数。"""
    if strategy_id not in BUILTIN_STRATEGIES:
        raise ValueError(f"未知策略: {strategy_id}")

    module_path, class_name = BUILTIN_STRATEGIES[strategy_id].rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    strategy = cls()

    for k, v in params.items():
        if hasattr(strategy, k):
            setattr(strategy, k, v)

    return strategy


def submit_backtest(
    run_id: str,
    strategy_id: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
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
        config_path,
    )


def _ensure_data(
    config: Config,
    symbols: list[str],
    start_date: str,
    end_date: str,
    record: RunRecord,
    start_time: float,
) -> None:
    """通过 AKShare DataPipeline 确保回测所需数据已下载到本地缓存。"""
    from cq.data.calendar import TradingCalendar
    from cq.data.pipeline import DataPipeline
    from cq.data.source import create_source
    from cq.data.store.parquet_store import ParquetStore

    store = ParquetStore(config.data.root_path)
    source = create_source("akshare")

    # 同步交易日历（本地没有时自动下载）
    calendar_days = store.read_calendar("SSE")
    if not calendar_days:
        record.current_date = "同步交易日历..."
        record.elapsed_seconds = time.monotonic() - start_time
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
        record.current_date = f"下载数据 {sym} ({i + 1}/{len(symbols)})..."
        record.elapsed_seconds = time.monotonic() - start_time
        try:
            pipeline.update_symbol(sym, end, start_date=start)
        except Exception as e:
            logger.warning(f"数据下载失败 {sym}: {e}，将使用已有本地数据")


def _run_backtest(
    run_id: str,
    strategy_id: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
    config_path: str,
) -> None:
    """在线程池中执行回测（同步阻塞）。"""
    record = run_store.get(run_id)
    if record is None:
        return

    record.status = "running"
    record.total_assets = initial_capital  # 下载阶段显示初始资金
    start_time = time.monotonic()

    def on_progress(current: int, total: int, trade_date: _date, total_assets: float) -> None:
        record.progress = int(current / total * 100)
        record.current_date = str(trade_date)
        record.total_assets = total_assets
        record.elapsed_seconds = time.monotonic() - start_time

    try:
        config = Config.from_yaml(config_path)
        config.engine.initial_capital = initial_capital
        config.risk.max_position_pct = risk_params.get("max_position_pct", 0.20)
        config.risk.min_cash_reserve = risk_params.get("min_cash_reserve", 0.05)
        config.risk.max_drawdown_stop = risk_params.get("max_drawdown_stop", 0.15)

        # 从 AKShare 下载/增量更新数据
        _ensure_data(config, symbols, start_date, end_date, record, start_time)

        strategy = load_strategy(strategy_id, strategy_params)

        for k, v in strategy_params.items():
            if hasattr(strategy, k):
                setattr(strategy, k, v)

        engine = BacktestEngine(config, progress_callback=on_progress)
        engine.add_strategy(strategy, symbols=symbols)
        result = engine.run(start_date, end_date)

        record.result = result
        record.status = "completed"
        record.progress = 100
        record.elapsed_seconds = time.monotonic() - start_time
        logger.info(f"回测完成 run_id={run_id}")

    except Exception as e:
        record.status = "failed"
        record.error = str(e)
        record.elapsed_seconds = time.monotonic() - start_time
        logger.error(f"回测失败 run_id={run_id}: {e}")
