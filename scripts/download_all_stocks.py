#!/usr/bin/env python
"""
批量下载全市场股票数据

用法：
    python scripts/download_all_stocks.py --exchange SH --limit 50
    python scripts/download_all_stocks.py --all --years 3
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

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

    calendar_days = store.read_calendar("SSE")
    if not calendar_days:
        logger.warning("本地无日历，将先同步日历")
        pipeline = DataPipeline(source, store, None)
        pipeline.sync_calendar("SSE")
        calendar_days = store.read_calendar("SSE")

    calendar = TradingCalendar(calendar_days)
    return DataPipeline(source, store, calendar)


def _get_all_symbols(exchange: str) -> list[str]:
    """获取交易所所有股票代码"""
    try:
        import akshare as ak

        if exchange == "SH":
            df = ak.stock_info_sh_name_code()
            codes = df["证券代码"].tolist()
            return [f"{code}.SH" for code in codes]
        elif exchange == "SZ":
            df = ak.stock_info_sz_name_code()
            codes = df["A股代码"].tolist()
            return [f"{code}.SZ" for code in codes]
        else:
            return []
    except Exception as e:
        logger.error(f"获取{exchange}股票列表失败: {e}")
        return []


@click.command()
@click.option("--exchange", "-e", type=click.Choice(["SH", "SZ", "all"]), default="all", help="交易所")
@click.option("--limit", "-l", type=int, default=None, help="限制下载数量")
@click.option("--years", "-y", type=int, default=3, help="下载最近N年数据")
@click.option("--workers", "-w", type=int, default=8, help="并行下载线程数")
@click.option("--config", "config_path", type=str, default="config/default.yaml", help="配置文件路径")
def main(
    exchange: str,
    limit: int | None,
    years: int,
    workers: int,
    config_path: str,
) -> None:
    config = Config.from_yaml(config_path)

    logger.remove()
    logger.add(sys.stderr, level=config.logging.level)

    pipeline = _build_pipeline(config)

    end_date = date.today()
    start_date = date(end_date.year - years, end_date.month, end_date.day)

    symbols_to_download = []

    if exchange == "all":
        logger.info("获取全市场股票列表...")
        sh_symbols = _get_all_symbols("SH")
        sz_symbols = _get_all_symbols("SZ")
        symbols_to_download = sh_symbols + sz_symbols
    else:
        logger.info(f"获取{exchange}股票列表...")
        symbols_to_download = _get_all_symbols(exchange)

    if not symbols_to_download:
        logger.error("未获取到股票列表")
        sys.exit(1)

    if limit:
        symbols_to_download = symbols_to_download[:limit]
        logger.info(f"限制下载数量为 {limit} 只股票")

    logger.info(f"准备下载 {len(symbols_to_download)} 只股票的数据 ({start_date} ~ {end_date})")

    results = pipeline.update_batch(
        symbols_to_download,
        end_date,
        start_date=start_date,
        max_workers=workers,
        force=False
    )

    total = sum(results.values())
    success = sum(1 for v in results.values() if v > 0)
    failed = len(results) - success

    logger.info(f"下载完成！成功: {success}, 失败: {failed}, 总记录数: {total}")


if __name__ == "__main__":
    main()
