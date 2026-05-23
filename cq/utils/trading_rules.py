"""
A股交易规则工具。

涨跌停价计算、板块判断等，集中管理避免散落各处。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class LimitPrices:
    limit_up: float
    limit_down: float


class AStockRules:
    """A股交易规则。"""

    # 涨跌停比例
    LIMIT_PCT_NORMAL = 0.10    # 普通股（主板）
    LIMIT_PCT_ST = 0.05        # 主板 ST / *ST
    LIMIT_PCT_WIDE = 0.20      # 科创板、创业板
    LIMIT_PCT_BSE = 0.30       # 北交所

    # 科创板前缀
    STAR_MARKET_PREFIX = "688"
    # 创业板前缀
    GEM_PREFIXES = ("300", "301")
    # 北交所前缀
    BSE_PREFIXES = ("4", "8", "9")

    @classmethod
    def get_limit_pct(
        cls,
        symbol: str,
        is_st: bool = False,
        trade_date: date | None = None,
    ) -> float:
        """
        返回 A 股日涨跌停比例。

        trade_date 预留给后续处理创业板注册制切换、新股前 5 日无涨跌幅等
        日期相关规则；当前日频回测默认按现行常规限制处理。
        """
        code, exchange = cls._split_symbol(symbol)

        if exchange == "BJ" or code.startswith(cls.BSE_PREFIXES):
            return cls.LIMIT_PCT_BSE
        if code.startswith(cls.STAR_MARKET_PREFIX) or code.startswith(cls.GEM_PREFIXES):
            return cls.LIMIT_PCT_WIDE
        if is_st:
            return cls.LIMIT_PCT_ST
        return cls.LIMIT_PCT_NORMAL

    @classmethod
    def calc_limit_prices(
        cls,
        pre_close: float,
        is_st: bool,
        symbol: str,
    ) -> LimitPrices:
        """
        根据昨收价、ST状态、股票代码计算涨跌停价。

        涨跌停价向下取整到分（0.01元精度）。
        """
        pct = cls.get_limit_pct(symbol, is_st=is_st)

        # 向下取整到分（使用 round 到2位小数，因精度问题用 int 处理）
        limit_up = round(int(pre_close * (1 + pct) * 100) / 100, 2)
        limit_down = round(int(pre_close * (1 - pct) * 100) / 100, 2)

        return LimitPrices(limit_up=limit_up, limit_down=limit_down)

    @classmethod
    def is_limit_up(cls, bar: "Bar") -> bool:  # type: ignore[name-defined]  # noqa: F821
        """当日是否涨停（收盘价 >= 涨停价）。"""
        return bar.close >= bar.limit_up

    @classmethod
    def is_limit_down(cls, bar: "Bar") -> bool:  # type: ignore[name-defined]  # noqa: F821
        """当日是否跌停（收盘价 <= 跌停价）。"""
        return bar.close <= bar.limit_down

    @classmethod
    def is_zt_open(cls, bar: "Bar") -> bool:  # type: ignore[name-defined]  # noqa: F821
        """开盘即涨停（无法买入）。"""
        return bar.open >= bar.limit_up

    @classmethod
    def is_dt_open(cls, bar: "Bar") -> bool:  # type: ignore[name-defined]  # noqa: F821
        """开盘即跌停。"""
        return bar.open <= bar.limit_down

    @staticmethod
    def round_to_lot(quantity: float) -> int:
        """将股数向下取整到100股整数倍。"""
        return int(quantity // 100) * 100

    @staticmethod
    def is_valid_lot(quantity: int) -> bool:
        """是否是合法手数（100的整数倍，且大于0）。"""
        return quantity > 0 and quantity % 100 == 0

    @staticmethod
    def _split_symbol(symbol: str) -> tuple[str, str]:
        parts = symbol.split(".")
        code = parts[0]
        exchange = parts[1].upper() if len(parts) > 1 else ""
        return code, exchange
