"""
ReplayFeed：历史数据回放 Feed（用于测试和纸上交易）。

实现 RealtimeFeed ABC，从 ParquetStore 读取指定日期范围的数据，
按顺序推送 Bar，完全模拟实时行情到达，不依赖 QMT 环境。

典型用途：
  - 集成测试：以历史数据驱动 LiveEngine，验证策略信号
  - 纸上交易：连接真实 LiveEngine 逻辑但不下真实订单
  - 回放分析：用过去某天数据重放，观察策略行为
"""

from __future__ import annotations

import time
from datetime import date

from loguru import logger

from cq.core.models import Bar
from cq.data.feed.realtime import RealtimeFeed
from cq.data.store.parquet_store import ParquetStore


class ReplayFeed(RealtimeFeed):
    """
    历史数据回放 Feed。

    参数：
        store       ParquetStore 实例
        symbols     标的列表
        start_date  回放开始日期
        end_date    回放结束日期（含）
        speed       回放速度倍数（默认 0 = 不等待，瞬间推送完所有 Bar）
                    speed=1.0 表示每根 Bar 间隔 1 秒，speed=2.0 表示 0.5 秒

    使用示例：
        feed = ReplayFeed(store, ["600519.SH"], date(2024,1,2), date(2024,1,31))
        feed.set_bar_callback(lambda bar: bar_queue.put(bar))
        feed.subscribe(["600519.SH"])  # 仅记录，不做网络操作
        feed.start()                   # 同步推送所有 Bar，推完后返回
    """

    def __init__(
        self,
        store: ParquetStore,
        symbols: list[str],
        start_date: date,
        end_date: date,
        speed: float = 0.0,
    ) -> None:
        super().__init__()
        self._store = store
        self._symbols = symbols
        self._start_date = start_date
        self._end_date = end_date
        self._speed = speed
        self._running = False

        # 预加载数据
        self._bars_by_date = self._load()

    # ── 公共接口 ─────────────────────────────────────────────────────────────────

    def subscribe(self, symbols: list[str]) -> None:
        """记录订阅列表（ReplayFeed 不做真实网络操作）。"""
        logger.debug(f"[ReplayFeed] subscribe: {symbols}")

    def unsubscribe(self, symbols: list[str]) -> None:
        logger.debug(f"[ReplayFeed] unsubscribe: {symbols}")

    def start(self) -> None:
        """
        按日期顺序推送所有 Bar（同步执行，推完后返回）。

        调用方通常在独立线程中运行此方法。
        """
        self._running = True
        total_bars = sum(len(v) for v in self._bars_by_date.values())
        logger.info(
            f"[ReplayFeed] 开始回放  {self._start_date}→{self._end_date}  "
            f"{len(self._bars_by_date)} 个交易日  {total_bars} 根 Bar"
        )

        for trade_date in sorted(self._bars_by_date):
            if not self._running:
                break
            bars = self._bars_by_date[trade_date]
            for bar in bars:
                if not self._running:
                    break
                if self._bar_callback:
                    self._bar_callback(bar)
                if self._speed > 0:
                    time.sleep(1.0 / self._speed)

        logger.info("[ReplayFeed] 回放结束")

    def stop(self) -> None:
        self._running = False

    def get_latest_bar(self, symbol: str) -> Bar | None:
        """返回最近一个交易日该标的的 Bar（测试用）。"""
        for trade_date in sorted(self._bars_by_date, reverse=True):
            for bar in self._bars_by_date[trade_date]:
                if bar.symbol == symbol:
                    return bar
        return None

    @property
    def trade_dates(self) -> list[date]:
        """回放涉及的所有交易日（升序）。"""
        return sorted(self._bars_by_date)

    @property
    def total_bars(self) -> int:
        return sum(len(v) for v in self._bars_by_date.values())

    # ── 内部方法 ─────────────────────────────────────────────────────────────────

    def _load(self) -> dict[date, list[Bar]]:
        """从 ParquetStore 加载数据，按交易日分组。"""
        from cq.data.feed.historical import HistoricalFeed

        feed = HistoricalFeed(self._store, self._symbols, self._start_date, self._end_date)
        result: dict[date, list[Bar]] = {}
        for trade_date, bars in feed.iter_by_date():
            result[trade_date] = bars
        return result
