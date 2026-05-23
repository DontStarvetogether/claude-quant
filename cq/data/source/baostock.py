"""
Baostock 数据源实现。

baostock 是免费的 A 股数据 API，覆盖沪深主要股票。
文档：http://baostock.com/baostock/index.php

注意事项：
- 股票代码格式转换：600519.SH → sh.600519
- 停牌判断：volume == 0 and amount == 0
- 不使用 baostock 内置复权（adjustflag="3" 即不复权），复权由 PriceAdjuster 统一处理
- 登录状态用单例管理，自动重连
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, datetime
from functools import wraps
from typing import Any, TypeVar

import pandas as pd
from loguru import logger

from cq.data.source.base import DataSource

F = TypeVar("F", bound=Callable[..., Any])

try:
    import baostock as bs  # type: ignore[import-untyped]
    HAS_BAOSTOCK = True
except ImportError:
    HAS_BAOSTOCK = False
    logger.warning("baostock 未安装，BaostockSource 不可用。pip install baostock")


def _require_baostock(func: F) -> F:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not HAS_BAOSTOCK:
            raise ImportError("请先安装 baostock: pip install baostock")
        return func(*args, **kwargs)
    return wrapper  # type: ignore[return-value]


class BaostockSession:
    """baostock 登录状态单例，自动重连。"""

    _instance: BaostockSession | None = None

    def __new__(cls) -> BaostockSession:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._logged_in = False
        return cls._instance

    def ensure_logged_in(self) -> None:
        if not self._logged_in:
            self._login()

    def _login(self) -> None:
        result = bs.login()
        if result.error_code != "0":
            raise ConnectionError(f"baostock 登录失败: {result.error_msg}")
        self._logged_in = True
        logger.debug("baostock 登录成功")

    def logout(self) -> None:
        if self._logged_in:
            bs.logout()
            self._logged_in = False


_session = BaostockSession() if HAS_BAOSTOCK else None
_lock = threading.Lock()  # baostock 不支持并发调用，需全局串行


class BaostockSource(DataSource):
    """baostock 数据源。"""

    # baostock 日线字段
    _BAR_FIELDS = "date,open,high,low,close,volume,amount,preclose,isST"

    @_require_baostock
    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        with _lock:
            _session.ensure_logged_in()  # type: ignore[union-attr]

            bs_code = self._to_bs_code(symbol)
            rs = bs.query_history_k_data_plus(
                bs_code,
                self._BAR_FIELDS,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                frequency="d",
                adjustflag="3",  # 不复权，由 PriceAdjuster 处理
            )

            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=rs.fields)
        return self._normalize_bars(df, symbol)

    @_require_baostock
    def fetch_adj_factors(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        with _lock:
            _session.ensure_logged_in()  # type: ignore[union-attr]

            bs_code = self._to_bs_code(symbol)
            rs = bs.query_adjust_factor(
                bs_code,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
            )

            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())

        if not rows:
            # 无复权因子（未除权过）→ 返回全 1.0
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=rs.fields)
        return self._normalize_adj_factors(df)

    @_require_baostock
    def fetch_trading_calendar(self, exchange: str, year: int) -> list[date]:
        with _lock:
            _session.ensure_logged_in()  # type: ignore[union-attr]

            rs = bs.query_trade_dates(
                start_date=f"{year}-01-01",
                end_date=f"{year}-12-31",
            )

            trading_dates = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                # row: [calendar_date, is_trading_day]
                if row[1] == "1":
                    trading_dates.append(datetime.strptime(row[0], "%Y-%m-%d").date())

        return sorted(trading_dates)

    @_require_baostock
    def fetch_stock_info(self, symbol: str) -> dict:
        _session.ensure_logged_in()  # type: ignore[union-attr]

        bs_code = self._to_bs_code(symbol)
        rs = bs.query_stock_basic(code=bs_code)
        if rs.error_code != "0" or not rs.next():
            raise ValueError(f"无法获取 {symbol} 的股票信息: {rs.error_msg}")

        row = dict(zip(rs.fields, rs.get_row_data(), strict=False))

        exchange = "SH" if symbol.endswith(".SH") else "SZ"
        code = symbol.split(".")[0]

        # 判断板块
        if code.startswith("688"):
            board = "star"
        elif code.startswith("300") or code.startswith("301"):
            board = "gem"
        else:
            board = "main"

        list_date = datetime.strptime(row.get("ipoDate", "19900101"), "%Y-%m-%d").date()
        delist_str = row.get("outDate", "")
        delist_date = datetime.strptime(delist_str, "%Y-%m-%d").date() if delist_str else None

        return {
            "name": row.get("code_name", ""),
            "list_date": list_date,
            "delist_date": delist_date,
            "exchange": exchange,
            "industry": row.get("industry", ""),
            "board": board,
        }

    # ── 私有工具 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_bs_code(symbol: str) -> str:
        """'600519.SH' → 'sh.600519'"""
        code, exchange = symbol.split(".")
        return f"{exchange.lower()}.{code}"

    @staticmethod
    def _normalize_bars(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """标准化 baostock 返回的日线数据。"""
        df = df.rename(columns={
            "date": "trade_date",
            "preclose": "pre_close",
            "isST": "is_st",
        })

        # 类型转换
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        for col in ["open", "high", "low", "close", "pre_close", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)
        df["is_st"] = df["is_st"].astype(str).str.strip() == "1"

        # 停牌判断：成交量和成交额均为0
        df["is_suspended"] = (df["volume"] == 0) & (df["amount"] == 0)

        # 过滤掉全 NaN 行（baostock 有时返回空行）
        df = df.dropna(subset=["open", "close"])

        return df[["trade_date", "open", "high", "low", "close", "volume",
                   "amount", "pre_close", "is_st", "is_suspended"]].reset_index(drop=True)

    @staticmethod
    def _normalize_adj_factors(df: pd.DataFrame) -> pd.DataFrame:
        """标准化复权因子数据。"""
        df = df.rename(columns={"dividOperateDate": "trade_date", "foreAdjustFactor": "adj_factor"})
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce").fillna(1.0)
        return df[["trade_date", "adj_factor"]].sort_values("trade_date").reset_index(drop=True)
