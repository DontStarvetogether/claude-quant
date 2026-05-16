"""DataSource ABC"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataSource(ABC):
    """
    数据源抽象基类。

    职责：从外部 API 获取原始数据，标准化列名和类型。
    不负责：缓存、复权、涨跌停计算。
    """

    @abstractmethod
    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        返回日线原始数据（未复权）。

        必须包含列：
            trade_date: date
            open, high, low, close: float（元，原始价格）
            volume: int（股）
            amount: float（元）
            pre_close: float（昨收，原始价）
            is_st: bool
            is_suspended: bool

        注意：不包含 limit_up/limit_down（由 DataPipeline 用 AStockRules 补充）。
        """

    @abstractmethod
    def fetch_adj_factors(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        返回复权因子。

        列：
            trade_date: date
            adj_factor: float（当日价格 × adj_factor = 前复权价格，相对最新日）
        """

    @abstractmethod
    def fetch_trading_calendar(self, exchange: str, year: int) -> list[date]:
        """
        返回指定年份的所有交易日列表，升序排列。
        exchange: "SSE"（上交所）| "SZSE"（深交所）
        """

    @abstractmethod
    def fetch_stock_info(self, symbol: str) -> dict:
        """
        返回股票基础信息字典：
            name: str
            list_date: date
            delist_date: date | None
            exchange: str ("SH" | "SZ" | "BJ")
            industry: str
            board: str ("main" | "star" | "gem" | "bj")
        """

    def fetch_suspended_dates(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> set[date]:
        """
        返回 symbol 在 [start_date, end_date] 区间内的停牌日期集合。

        默认返回空集合（子类可覆盖以接入显式停牌数据源）。
        """
        return set()
