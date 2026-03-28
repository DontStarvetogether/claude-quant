#!/usr/bin/env python
"""
运行回测并输出报告。

用法：
    python scripts/run_backtest.py --strategy double_ma --symbols 600519.SH --start 2022-01-01 --end 2024-12-31
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from loguru import logger

from cq.engine.backtest_engine import BacktestEngine
from cq.utils.config import Config


BUILTIN_STRATEGIES = {
    "double_ma": "cq.strategy.examples.double_ma.DoubleMaStrategy",
}


def _load_strategy(name: str):
    if name in BUILTIN_STRATEGIES:
        module_path, class_name = BUILTIN_STRATEGIES[name].rsplit(".", 1)
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, class_name)()
    raise ValueError(
        f"未知策略: {name}。内置策略: {list(BUILTIN_STRATEGIES.keys())}"
    )


@click.command()
@click.option("--strategy", "-st", required=True, help="策略名称（如 double_ma）或 Python 类路径")
@click.option("--symbols", "-s", required=True, multiple=True, help="股票代码")
@click.option("--start", required=True, help="开始日期 YYYY-MM-DD")
@click.option("--end", required=True, help="结束日期 YYYY-MM-DD")
@click.option("--capital", type=float, default=None, help="初始资金（覆盖配置）")
@click.option("--config", "config_path", default="config/local.yaml", help="配置文件路径")
@click.option("--output", "-o", default=None, help="结果输出 JSON 文件路径")
def main(
    strategy: str,
    symbols: tuple[str, ...],
    start: str,
    end: str,
    capital: float | None,
    config_path: str,
    output: str | None,
) -> None:
    config = Config.from_yaml(config_path)
    if capital is not None:
        config.engine.initial_capital = capital

    logger.remove()
    logger.add(sys.stderr, level=config.logging.level)

    strat = _load_strategy(strategy)
    engine = BacktestEngine(config)
    engine.add_strategy(strat, symbols=list(symbols))

    result = engine.run(start, end)
    print(result.summary())

    if output:
        import json
        with open(output, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"结果已保存到 {output}")


if __name__ == "__main__":
    main()
