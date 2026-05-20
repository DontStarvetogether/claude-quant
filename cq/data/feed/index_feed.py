"""
IndexFeed：指数数据加载器。

与 HistoricalFeed 兼容同一 ParquetStore 存储格式，
加载指定指数的日线数据，提供日收益率序列供基准对比。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from loguru import logger

from cq.data.store.parquet_store import ParquetStore


class IndexFeed:
    """加载单个指数的历史日线数据。"""

    def __init__(
        self,
        store: ParquetStore,
        index_code: str,
        start_date: date,
        end_date: date,
    ) -> None:
        self._symbol = index_code
        self._df = store.read_daily_bars(index_code, adjust="qfq")  # 指数不需要复权
        if self._df.empty:
            logger.warning(f"指数 {index_code} 数据为空")
            self._returns: pd.Series = pd.Series(dtype=float)
            self._close: pd.Series = pd.Series(dtype=float)
            self._dates: list[date] = []
            return

        self._df = self._df.sort_values("trade_date")
        mask = (self._df["trade_date"] >= pd.Timestamp(start_date)) & (
            self._df["trade_date"] <= pd.Timestamp(end_date)
        )
        self._df = self._df[mask]

        self._close = self._df.set_index("trade_date")["close"]
        self._returns = self._close.pct_change().dropna()
        self._dates = self._df["trade_date"].dt.date.tolist()

        logger.info(
            f"加载指数 {index_code}：{len(self._df)} 个交易日，{start_date} → {end_date}"
        )

    @property
    def returns(self) -> pd.Series:
        """日收益率序列，index=trade_date。"""
        return self._returns

    @property
    def close(self) -> pd.Series:
        """收盘价序列，index=trade_date。"""
        return self._close

    @property
    def dates(self) -> list[date]:
        return self._dates

    def total_return(self) -> float:
        """区间总收益率。"""
        if len(self._close) < 2:
            return 0.0
        return float(
            (self._close.iloc[-1] - self._close.iloc[0]) / self._close.iloc[0]
        )

    def annual_return(self) -> float:
        """年化收益率（252交易日）。"""
        if len(self._close) < 2:
            return 0.0
        total = self.total_return()
        n_days = len(self._close) - 1
        if n_days <= 0:
            return 0.0
        return float((1 + total) ** (252 / n_days) - 1)
