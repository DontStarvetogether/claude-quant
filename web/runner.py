"""
回测执行器。

BacktestEngine.run() 是同步阻塞的，在线程池中执行，
通过 progress_callback 将进度写入 RunRecord（内存共享）。
"""

from __future__ import annotations

import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from loguru import logger

from cq.engine.backtest_engine import BacktestEngine
from cq.utils.config import Config, DataConfig, EngineConfig, RiskConfig
from web.store import RunRecord, run_store

# 内置策略注册表：strategy_id → Python 类路径
BUILTIN_STRATEGIES: dict[str, str] = {
    "double_ma": "cq.strategy.examples.double_ma.DoubleMaStrategy",
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

    # 设置参数（覆盖 on_init 的默认值）
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
    start_time = time.monotonic()

    def on_progress(current: int, total: int, trade_date: date, total_assets: float) -> None:
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

        strategy = load_strategy(strategy_id, strategy_params)

        # 注入进度回调后，在 on_init 之前设置参数
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
