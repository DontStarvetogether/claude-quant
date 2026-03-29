"""自动数据更新服务"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from cq.data.calendar import TradingCalendar
from cq.data.pipeline import DataPipeline
from cq.data.source import create_source
from cq.data.store.parquet_store import ParquetStore
from cq.utils.config import Config


class DataUpdateService:
    """自动数据更新服务"""

    def __init__(self, config: Config):
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self._pipeline: Optional[DataPipeline] = None
        self._running = False

    def _build_pipeline(self) -> DataPipeline:
        """构建数据管道"""
        if self._pipeline is None:
            store = ParquetStore(self.config.data.root_path)
            source = create_source(self.config.data.source)

            calendar_days = store.read_calendar("SSE")
            if not calendar_days:
                logger.warning("本地无日历，将先同步日历")
                pipeline = DataPipeline(source, store, None)
                pipeline.sync_calendar("SSE")
                calendar_days = store.read_calendar("SSE")

            calendar = TradingCalendar(calendar_days)
            self._pipeline = DataPipeline(source, store, calendar)

        return self._pipeline

    async def update_stock_names(self) -> None:
        """每月更新股票名称"""
        try:
            logger.info("开始更新股票名称...")
            store = ParquetStore(self.config.data.root_path)

            # 导入同步函数
            from web.routers.symbols import _sync_names_from_akshare
            _sync_names_from_akshare(store)

            logger.info("股票名称更新完成")
        except Exception as e:
            logger.error(f"更新股票名称失败: {e}")

    async def update_kline_data(self) -> None:
        """每周更新K线数据"""
        try:
            logger.info("开始更新K线数据...")
            pipeline = self._build_pipeline()

            # 获取本地已有数据的股票列表
            from web.routers.symbols import _local_symbols
            symbols = _local_symbols(pipeline._store)

            if not symbols:
                logger.warning("没有本地数据，跳过K线更新")
                return

            # 更新最近1个月的数据
            from datetime import date, timedelta
            end_date = date.today()
            start_date = end_date - timedelta(days=30)

            logger.info(f"更新 {len(symbols)} 只股票的数据 ({start_date} ~ {end_date})")

            # 批量更新
            results = pipeline.update_batch(
                symbols,
                end_date,
                start_date=start_date,
                max_workers=8,
                force=False
            )

            total = sum(results.values())
            success = sum(1 for v in results.values() if v > 0)
            logger.info(f"K线数据更新完成：成功 {success} 只，总记录数 {total}")

        except Exception as e:
            logger.error(f"更新K线数据失败: {e}")

    async def update_all_stocks(self, limit: int = 100) -> None:
        """按需更新全市场股票"""
        try:
            logger.info(f"开始下载 {limit} 只新股票...")
            pipeline = self._build_pipeline()

            # 获取全市场股票列表
            from scripts.download_all_stocks import _get_all_symbols
            sh_symbols = _get_all_symbols("SH")[:limit//2]
            sz_symbols = _get_all_symbols("SZ")[:limit//2]
            symbols_to_download = sh_symbols + sz_symbols

            if not symbols_to_download:
                logger.warning("未获取到股票列表")
                return

            # 下载最近3年数据
            from datetime import date, timedelta
            end_date = date.today()
            start_date = end_date - timedelta(days=365*3)

            logger.info(f"下载 {len(symbols_to_download)} 只股票的数据 ({start_date} ~ {end_date})")

            # 批量下载
            results = pipeline.update_batch(
                symbols_to_download,
                end_date,
                start_date=start_date,
                max_workers=8,
                force=False
            )

            total = sum(results.values())
            success = sum(1 for v in results.values() if v > 0)
            logger.info(f"新股票下载完成：成功 {success} 只，总记录数 {total}")

        except Exception as e:
            logger.error(f"下载新股票失败: {e}")

    def start(self) -> None:
        """启动定时任务"""
        if self._running:
            logger.warning("数据更新服务已在运行")
            return

        self._running = True

        # 每月1日凌晨2点更新股票名称
        self.scheduler.add_job(
            self.update_stock_names,
            'cron',
            day=1,
            hour=2,
            minute=0,
            id='update_stock_names',
            name='每月更新股票名称'
        )

        # 每周一凌晨3点更新K线数据
        self.scheduler.add_job(
            self.update_kline_data,
            'cron',
            day_of_week='mon',
            hour=3,
            minute=0,
            id='update_kline_data',
            name='每周更新K线数据'
        )

        # 启动时立即执行一次K线更新（延迟5分钟）
        self.scheduler.add_job(
            self.update_kline_data,
            'date',
            run_date=datetime.now(),
            id='initial_kline_update',
            name='初始K线更新'
        )

        self.scheduler.start()
        logger.info("数据更新服务已启动")

    def stop(self) -> None:
        """停止定时任务"""
        if not self._running:
            return

        self.scheduler.shutdown()
        self._running = False
        logger.info("数据更新服务已停止")

    async def trigger_update_now(self, update_type: str = 'kline') -> None:
        """立即触发更新"""
        if update_type == 'names':
            await self.update_stock_names()
        elif update_type == 'kline':
            await self.update_kline_data()
        elif update_type == 'all':
            await self.update_all_stocks(limit=100)