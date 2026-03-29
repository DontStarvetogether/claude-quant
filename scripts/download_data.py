#!/usr/bin/env python
"""
下载并存储历史 K 线数据。

用法：
    python scripts/download_data.py --symbols 600519.SH 000001.SZ --years 3
    python scripts/download_data.py --symbols 600519.SH --start 2020-01-01
    python scripts/download_data.py --sync-calendar  # 只同步日历
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

# 项目根目录加入 path
sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from loguru import logger

from cq.data.calendar import TradingCalendar
from cq.data.pipeline import DataPipeline
from cq.data.source import create_source
from cq.data.store.parquet_store import ParquetStore
from cq.utils.config import Config


def _build_pipeline(config: Config) -> DataPipeline:
    store = ParquetStore(config.data.root_path)
    source = create_source(config.data.source)

    # 加载日历
    calendar_days = store.read_calendar("SSE")
    if not calendar_days:
        logger.warning("本地无日历，将先同步日历")
        pipeline = DataPipeline(source, store, None)  # type: ignore[arg-type]
        pipeline.sync_calendar("SSE")
        calendar_days = store.read_calendar("SSE")

    calendar = TradingCalendar(calendar_days)
    return DataPipeline(source, store, calendar)


@click.command()
@click.option("--symbols", "-s", multiple=True, help="股票代码，如 600519.SH 000001.SZ")
@click.option("--years", "-y", type=int, default=None, help="下载最近 N 年数据")
@click.option("--start", type=str, default=None, help="开始日期 YYYY-MM-DD")
@click.option("--end", type=str, default=None, help="结束日期 YYYY-MM-DD（默认今天）")
@click.option("--force", is_flag=True, default=False, help="强制重新全量下载")
@click.option("--sync-calendar", is_flag=True, default=False, help="同步交易日历")
@click.option("--workers", type=int, default=8, help="并行下载线程数")
@click.option("--config", "config_path", type=str, default="config/local.yaml", help="配置文件路径")
def main(
    symbols: tuple[str, ...],
    years: int | None,
    start: str | None,
    end: str | None,
    force: bool,
    sync_calendar: bool,
    workers: int,
    config_path: str,
) -> None:
    config = Config.from_yaml(config_path)

    # 设置日志
    logger.remove()
    logger.add(sys.stderr, level=config.logging.level)

    pipeline = _build_pipeline(config)

    # 同步日历
    if sync_calendar:
        logger.info("同步交易日历...")
        current_year = date.today().year
        pipeline.sync_calendar("SSE", years=list(range(2000, current_year + 1)))
        pipeline.sync_calendar("SZSE", years=list(range(2000, current_year + 1)))
        logger.info("日历同步完成")
        if not symbols:
            return

    if not symbols:
        logger.error("请指定股票代码，如 --symbols 600519.SH 000001.SZ")
        sys.exit(1)

    # 计算日期范围
    end_date = date.fromisoformat(end) if end else date.today()

    if years is not None:
        start_date = date(end_date.year - years, end_date.month, end_date.day)
    elif start is not None:
        start_date = date.fromisoformat(start)
    else:
        start_date = None  # update_symbol 会使用上市日期

    logger.info(f"下载 {len(symbols)} 只股票：{list(symbols)}")

    if len(symbols) == 1:
        result = pipeline.update_symbol(symbols[0], end_date, start_date=start_date, force=force)
        logger.info(f"完成，新增 {result} 条记录")
    else:
        results = pipeline.update_batch(list(symbols), end_date, start_date=start_date, max_workers=workers, force=force)
        total = sum(results.values())
        logger.info(f"全部完成，共新增 {total} 条记录")


if __name__ == "__main__":
    main()
