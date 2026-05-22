"""内置策略注册表。"""

from __future__ import annotations

import importlib
from typing import Any

from cq.strategy.base import Strategy


BUILTIN_STRATEGIES: dict[str, str] = {
    "double_ma": "cq.strategy.examples.double_ma.DoubleMaStrategy",
    "rsi": "cq.strategy.examples.rsi.RsiStrategy",
    "bollinger": "cq.strategy.examples.bollinger.BollingerStrategy",
    "momentum": "cq.strategy.examples.momentum.MomentumStrategy",
    "trend_rank": "cq.strategy.examples.trend_rank.TrendRankStrategy",
}


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
            {"name": "period", "type": "int", "default": 14, "label": "RSI 周期", "min": 5, "max": 60, "step": 1},
            {"name": "oversold", "type": "float", "default": 35, "label": "超卖阈值", "min": 10, "max": 40, "step": 1},
            {"name": "overbought", "type": "float", "default": 65, "label": "超买阈值", "min": 60, "max": 90, "step": 1},
            {"name": "position_pct", "type": "float", "default": 0.9, "label": "仓位比例", "min": 0.1, "max": 1.0, "step": 0.1},
            {"name": "trend_filter_enabled", "type": "bool", "default": True, "label": "趋势过滤"},
            {"name": "trend_ma", "type": "int", "default": 120, "label": "趋势均线", "min": 60, "max": 250, "step": 5},
        ],
    },
    "bollinger": {
        "name": "布林带策略",
        "description": "价格触及下轨买入，触及上轨卖出（均值回归）",
        "params": [
            {"name": "period", "type": "int", "default": 20, "label": "均线周期", "min": 5, "max": 60, "step": 1},
            {"name": "std_dev", "type": "float", "default": 2.0, "label": "标准差倍数", "min": 1.0, "max": 3.0, "step": 0.5},
            {"name": "position_pct", "type": "float", "default": 0.9, "label": "仓位比例", "min": 0.1, "max": 1.0, "step": 0.1},
            {"name": "trend_filter_enabled", "type": "bool", "default": True, "label": "趋势过滤"},
            {"name": "trend_ma", "type": "int", "default": 120, "label": "趋势均线", "min": 60, "max": 250, "step": 5},
        ],
    },
    "momentum": {
        "name": "动量策略",
        "description": "N 日涨幅超阈值买入，跌破阈值卖出（趋势跟随）",
        "params": [
            {"name": "lookback", "type": "int", "default": 20, "label": "回看天数", "min": 5, "max": 60, "step": 1},
            {"name": "buy_threshold", "type": "float", "default": 0.05, "label": "买入阈值", "min": 0.01, "max": 0.3, "step": 0.01},
            {"name": "sell_threshold", "type": "float", "default": -0.05, "label": "卖出阈值", "min": -0.3, "max": -0.01, "step": 0.01},
            {"name": "position_pct", "type": "float", "default": 0.9, "label": "仓位比例", "min": 0.1, "max": 1.0, "step": 0.1},
        ],
    },
    "trend_rank": {
        "name": "趋势排名选股",
        "description": "按趋势强度和成交额过滤股票池，买入排名靠前标的",
        "params": [
            {"name": "momentum_lookback", "type": "int", "default": 60, "label": "动量周期", "min": 20, "max": 120, "step": 5},
            {"name": "fast_ma", "type": "int", "default": 20, "label": "快均线", "min": 5, "max": 60, "step": 1},
            {"name": "slow_ma", "type": "int", "default": 60, "label": "慢均线", "min": 20, "max": 200, "step": 5},
            {"name": "min_avg_amount", "type": "float", "default": 50000000, "label": "最低均成交额", "min": 10000000, "max": 500000000, "step": 10000000},
            {"name": "min_momentum", "type": "float", "default": 0.03, "label": "最低涨幅", "min": -0.2, "max": 0.5, "step": 0.01},
            {"name": "top_n", "type": "int", "default": 30, "label": "排名前N", "min": 5, "max": 100, "step": 5},
            {"name": "rank_exit_n", "type": "int", "default": 60, "label": "退出排名N", "min": 10, "max": 200, "step": 5},
            {"name": "max_holdings", "type": "int", "default": 5, "label": "最多持仓数", "min": 1, "max": 20, "step": 1},
            {"name": "position_pct", "type": "float", "default": 0.2, "label": "单股仓位", "min": 0.05, "max": 0.5, "step": 0.05},
            {"name": "trailing_stop", "type": "float", "default": 0.08, "label": "移动止损", "min": 0.03, "max": 0.3, "step": 0.01},
            {"name": "volatility_lookback", "type": "int", "default": 20, "label": "波动周期", "min": 5, "max": 60, "step": 1},
        ],
    },
}


def get_strategy_list() -> list[dict]:
    return [{"id": sid, **meta} for sid, meta in STRATEGY_METADATA.items()]


def validate_strategy_params(strategy_id: str, params: dict[str, Any]) -> dict[str, Any]:
    if strategy_id not in STRATEGY_METADATA:
        raise ValueError(f"未知策略: {strategy_id}")
    allowed = {p["name"] for p in STRATEGY_METADATA[strategy_id]["params"]}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError(f"策略 {strategy_id} 不支持参数: {', '.join(unknown)}")
    return dict(params)


def load_strategy(strategy_id: str, params: dict[str, Any] | None = None) -> Strategy:
    if strategy_id not in BUILTIN_STRATEGIES:
        raise ValueError(f"未知策略: {strategy_id}")

    module_path, class_name = BUILTIN_STRATEGIES[strategy_id].rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    strategy = cls()
    strategy._configured_params = validate_strategy_params(strategy_id, params or {})
    return strategy
