"""
交易日历。

使用 frozenset 存储所有交易日，O(1) 判断是否为交易日。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from typing import Optional

from loguru import logger


class TradingCalendar:
    """
    A股交易日历。

    内部维护两个数据结构：
    - _date_set：frozenset，O(1) 判断交易日
    - _date_list：有序列表，O(log n) 查找前/后交易日
    """

    def __init__(self, trading_days: list[date]) -> None:
        if not trading_days:
            raise ValueError("交易日历不能为空")
        self._date_list: list[date] = sorted(trading_days)
        self._date_set: frozenset[date] = frozenset(self._date_list)

    def is_trading_day(self, d: date) -> bool:
        return d in self._date_set

    def next_trading_day(self, d: date, n: int = 1) -> date:
        """返回 d 之后第 n 个交易日（不含 d 本身）。"""
        if n <= 0:
            raise ValueError(f"n 必须 > 0，got {n}")
        idx = bisect_right(self._date_list, d)
        target_idx = idx + n - 1
        if target_idx >= len(self._date_list):
            raise ValueError(f"{d} 之后不足 {n} 个交易日（日历截止 {self._date_list[-1]}）")
        return self._date_list[target_idx]

    def prev_trading_day(self, d: date, n: int = 1) -> date:
        """返回 d 之前第 n 个交易日（不含 d 本身）。"""
        if n <= 0:
            raise ValueError(f"n 必须 > 0，got {n}")
        idx = bisect_left(self._date_list, d)
        target_idx = idx - n
        if target_idx < 0:
            raise ValueError(f"{d} 之前不足 {n} 个交易日（日历起始 {self._date_list[0]}）")
        return self._date_list[target_idx]

    def trading_days_between(self, start: date, end: date) -> list[date]:
        """返回 [start, end] 之间的所有交易日（含两端）。"""
        left = bisect_left(self._date_list, start)
        right = bisect_right(self._date_list, end)
        return self._date_list[left:right]

    def count_trading_days(self, start: date, end: date) -> int:
        """返回 [start, end] 之间的交易日数量（含两端）。"""
        return len(self.trading_days_between(start, end))

    @property
    def start_date(self) -> date:
        return self._date_list[0]

    @property
    def end_date(self) -> date:
        return self._date_list[-1]

    def __len__(self) -> int:
        return len(self._date_list)

    def __contains__(self, d: date) -> bool:
        return self.is_trading_day(d)
