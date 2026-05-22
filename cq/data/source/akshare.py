"""
akshare 数据源实现。

akshare 是国内量化社区最常用的免费 A 股数据库，以 EastMoney / Sina 为主要后端，
活跃维护（2025）。相比 baostock 更可靠，无需注册，支持断线重连。

文档：https://www.akshare.xyz/

注意事项：
- 股票代码格式：600519.SH → "600519"（去掉交易所后缀）
- 成交量单位：akshare 返回"手"（1手=100股），内部统一转换为"股"
- is_st：akshare 无历史 ST 数据，统一设为 False（TODO：接入历史 ST API）
- 停牌判断：优先使用 stock_tfp_em 显式停牌数据，回退到 volume==0 推断
- adj_factor 计算：raw_close / qfq_close（与 PriceAdjuster 约定一致）
"""

from __future__ import annotations

import time
from datetime import date, datetime
from functools import wraps
from typing import Any, Callable, ClassVar, TypeVar

import numpy as np
import pandas as pd
from loguru import logger

from cq.data.source.base import DataSource

F = TypeVar("F", bound=Callable[..., Any])

try:
    import akshare as ak  # type: ignore[import-untyped]
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False
    logger.warning("akshare 未安装，AkshareSource 不可用。pip install akshare")


# 不干预代理设置，让 requests 使用系统代理（Clash 等）


def _require_akshare(func: F) -> F:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not HAS_AKSHARE:
            raise ImportError("请先安装 akshare: pip install akshare")
        return func(*args, **kwargs)
    return wrapper  # type: ignore[return-value]


class AkshareSource(DataSource):
    """
    akshare 数据源。

    使用 East Money 接口获取 A 股日线行情和复权因子。
    """

    # 全量交易日历缓存（ak.tool_trade_date_hist_sina 返回全部历史，只需一次请求）
    _calendar_cache: ClassVar[list[date] | None] = None
    # 停复牌记录缓存：{纯数字代码: [(停牌起始日, 停牌截止日), ...]}
    _suspension_cache: ClassVar[dict[str, list[tuple[date, date | None]]] | None] = None
    # 当前 ST 股代码集合缓存
    _st_codes_cache: ClassVar[set[str] | None] = None

    @staticmethod
    def _to_ak_code(symbol: str) -> str:
        """'600519.SH' → '600519'（akshare 仅需数字代码）"""
        return symbol.split(".")[0]

    @staticmethod
    def _to_sina_code(symbol: str) -> str:
        """'600519.SH' → 'sh600519'（新浪接口格式）"""
        code, exchange = symbol.split(".")
        prefix = "sh" if exchange == "SH" else "sz"
        return f"{prefix}{code}"

    @_require_akshare
    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        # 优先使用新浪源（stock_zh_a_daily），速度快且稳定
        try:
            return self._fetch_daily_bars_sina(symbol, start_date, end_date)
        except Exception as e_sina:
            logger.debug(f"{symbol} 新浪源失败: {e_sina}，尝试东方财富源")

        # 回退到东方财富源
        code = self._to_ak_code(symbol)
        # 尝试 stock_zh_a_hist（个股），失败则尝试 index_zh_a_hist（指数）
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="",
            )
            if df is not None and not df.empty:
                return self._normalize_bars(df)
        except Exception:
            pass
        # 指数数据（如 000300.SH 沪深300）
        try:
            df = ak.index_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )
            if df is not None and not df.empty:
                return self._normalize_bars(df)
        except Exception:
            pass
        # 新浪指数历史行情 fallback（覆盖创业板指等深证指数）
        try:
            df = ak.stock_zh_index_daily(symbol=self._to_sina_code(symbol))
            if df is not None and not df.empty:
                return self._normalize_sina_index_bars(df, start_date, end_date)
        except Exception:
            pass
        # 中证指数官网数据 fallback（可覆盖沪深300等常用基准；字段同样含 OHLC）
        try:
            df = ak.stock_zh_index_hist_csindex(
                symbol=code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )
            if df is not None and not df.empty:
                return self._normalize_bars(df)
        except Exception:
            pass
        raise ValueError(f"akshare 获取 {symbol} 日线失败（个股和指数均无数据）")

    def _fetch_daily_bars_sina(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """通过新浪源（stock_zh_a_daily）获取日线数据。"""
        sina_code = self._to_sina_code(symbol)
        df = ak.stock_zh_a_daily(symbol=sina_code, adjust="")

        if df is None or df.empty:
            return pd.DataFrame()

        # 过滤日期范围
        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()

        if df.empty:
            return pd.DataFrame()

        # 标准化列名（新浪源列名已是英文，volume 已是股）
        for col in ["open", "high", "low", "close", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)

        # pre_close 通过前一行 close 计算
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["pre_close"] = df["close"].shift(1)
        if len(df) > 0:
            df.loc[df.index[0], "pre_close"] = df.loc[df.index[0], "open"]

        df["is_st"] = self._is_st_stock(symbol)

        # 停牌判断
        volume_zero = (df["volume"] == 0) & (df["amount"].fillna(0) == 0)
        suspended_dates = self.fetch_suspended_dates(symbol, start_date, end_date)
        if suspended_dates:
            df["is_suspended"] = df["trade_date"].isin(suspended_dates) | volume_zero
        else:
            df["is_suspended"] = volume_zero

        # 涨跌停判断
        df["limit_up"] = False
        df["limit_down"] = False

        keep_cols = [
            "trade_date", "open", "high", "low", "close", "volume",
            "amount", "pre_close", "is_st", "is_suspended",
            "limit_up", "limit_down",
        ]
        return df[[c for c in keep_cols if c in df.columns]].reset_index(drop=True)

    @staticmethod
    def _normalize_sina_index_bars(
        df: pd.DataFrame,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """标准化新浪指数历史行情数据（stock_zh_index_daily）。"""
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)].copy()
        if df.empty:
            return pd.DataFrame()

        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0).astype(int)
        df["amount"] = 0.0
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["pre_close"] = df["close"].shift(1)
        df.loc[df.index[0], "pre_close"] = df.loc[df.index[0], "open"]
        df["is_st"] = False
        df["is_suspended"] = False

        return df[[
            "trade_date", "open", "high", "low", "close",
            "volume", "amount", "pre_close", "is_st", "is_suspended",
        ]].dropna(subset=["open", "close"]).reset_index(drop=True)

    @_require_akshare
    def fetch_adj_factors(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        通过对比不复权 / 前复权价格来计算 adj_factor。

        adj_factor[d] = raw_close[d] / qfq_close[d]

        约定（与 PriceAdjuster 一致）：
          - 最新日期的 adj_factor ≈ 1.0（以最新收盘为基准）
          - 除权日之前的 adj_factor > 1.0（历史价格需向下调整）
        """
        # 优先用新浪源
        try:
            return self._fetch_adj_factors_sina(symbol, start_date, end_date)
        except Exception as e_sina:
            logger.debug(f"{symbol} 新浪源复权因子失败: {e_sina}，尝试东方财富源")

        # 回退到东方财富源
        code = self._to_ak_code(symbol)
        try:
            raw_df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="",
            )
        except Exception as e:
            raise ValueError(f"akshare 获取 {symbol} 不复权数据失败: {e}") from e
        finally:
            time.sleep(0.1)

        if raw_df is None or raw_df.empty:
            return pd.DataFrame()

        try:
            qfq_df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception as e:
            raise ValueError(f"akshare 获取 {symbol} 前复权数据失败: {e}") from e
        finally:
            time.sleep(0.1)

        if qfq_df is None or qfq_df.empty:
            return pd.DataFrame()

        return self._compute_adj_factors(raw_df, qfq_df)

    def _fetch_adj_factors_sina(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """通过新浪源对比不复权/前复权价格计算 adj_factor。"""
        sina_code = self._to_sina_code(symbol)

        raw_df = ak.stock_zh_a_daily(symbol=sina_code, adjust="")
        qfq_df = ak.stock_zh_a_daily(symbol=sina_code, adjust="qfq")

        if raw_df is None or raw_df.empty or qfq_df is None or qfq_df.empty:
            return pd.DataFrame()

        # 过滤日期范围
        raw_df["trade_date"] = pd.to_datetime(raw_df["date"]).dt.date
        qfq_df["trade_date"] = pd.to_datetime(qfq_df["date"]).dt.date
        raw_df = raw_df[(raw_df["trade_date"] >= start_date) & (raw_df["trade_date"] <= end_date)]
        qfq_df = qfq_df[(qfq_df["trade_date"] >= start_date) & (qfq_df["trade_date"] <= end_date)]

        if raw_df.empty or qfq_df.empty:
            return pd.DataFrame()

        # 按日期对齐
        raw_close = raw_df.set_index("trade_date")["close"].astype(float)
        qfq_close = qfq_df.set_index("trade_date")["close"].astype(float)
        common_dates = raw_close.index.intersection(qfq_close.index)

        if common_dates.empty:
            return pd.DataFrame()

        adj_factor = raw_close[common_dates] / qfq_close[common_dates]
        adj_factor = adj_factor.replace([np.inf, -np.inf], 1.0).fillna(1.0)

        result = pd.DataFrame({
            "trade_date": common_dates,
            "adj_factor": adj_factor.values,
        })
        return result

    @_require_akshare
    def fetch_trading_calendar(self, exchange: str, year: int) -> list[date]:
        """
        从 Sina 获取全量 A 股交易日历（上深两所相同），按 year 过滤。
        结果缓存至类变量，避免重复 HTTP 请求。
        """
        if AkshareSource._calendar_cache is None:
            try:
                cal_df = ak.tool_trade_date_hist_sina()
            except Exception as e:
                raise ValueError(f"akshare 获取交易日历失败: {e}") from e
            finally:
                time.sleep(0.1)

            if cal_df is None or cal_df.empty:
                AkshareSource._calendar_cache = []
            else:
                # 列名因版本不同可能为 '交易日期' 或 'trade_date'
                col = cal_df.columns[0]
                AkshareSource._calendar_cache = (
                    pd.to_datetime(cal_df[col]).dt.date.tolist()
                )
            logger.debug(f"akshare 日历缓存：{len(AkshareSource._calendar_cache)} 个交易日")

        return sorted(d for d in AkshareSource._calendar_cache if d.year == year)

    @_require_akshare
    def fetch_stock_info(self, symbol: str) -> dict:
        code = self._to_ak_code(symbol)
        try:
            info_df = ak.stock_individual_info_em(symbol=code)
        except Exception as e:
            raise ValueError(f"akshare 获取 {symbol} 股票信息失败: {e}") from e
        finally:
            time.sleep(0.1)

        if info_df is None or info_df.empty:
            raise ValueError(f"akshare 返回空股票信息: {symbol}")

        # info_df 格式: columns=['item', 'value'] 或 ['指标', '值']
        item_col = info_df.columns[0]
        val_col = info_df.columns[1]
        info = dict(zip(info_df[item_col].astype(str), info_df[val_col].astype(str)))

        exchange = "SH" if symbol.endswith(".SH") else ("SZ" if symbol.endswith(".SZ") else "BJ")
        board = self._detect_board(code)

        # 上市时间字段名可能不同版本有差异
        list_date_str = (
            info.get("上市时间")
            or info.get("上市日期")
            or info.get("首发上市日期")
            or ""
        )
        try:
            list_date: date = datetime.strptime(str(list_date_str).strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            list_date = date(2000, 1, 1)

        industry = (
            info.get("行业")
            or info.get("所属行业")
            or info.get("行业板块")
            or ""
        )

        return {
            "name": info.get("股票简称", ""),
            "list_date": list_date,
            "delist_date": None,  # akshare 无历史退市日期字段
            "exchange": exchange,
            "industry": str(industry),
            "board": board,
        }

    @_require_akshare
    def fetch_suspended_dates(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> set[date]:
        """
        返回 symbol 在 [start_date, end_date] 区间内的显式停牌日期集合。

        数据来源：ak.stock_tfp_em()（东方财富停复牌信息）。
        每条记录包含 [停牌时间, 停牌截止时间] 区间，展开为逐日日期。
        """
        self._load_suspension_table()
        code = self._to_ak_code(symbol)
        records = self._suspension_cache.get(code, [])
        if not records:
            return set()

        result: set[date] = set()
        for susp_start, susp_end in records:
            # 与查询区间取交集
            effective_start = max(susp_start, start_date)
            effective_end = min(susp_end, end_date) if susp_end else end_date
            if effective_start > effective_end:
                continue
            # 展开为逐日
            d = effective_start
            while d <= effective_end:
                result.add(d)
                d += pd.Timedelta(days=1).to_pytimedelta()
        return result

    @classmethod
    def _load_suspension_table(cls) -> None:
        """加载并缓存停复牌记录表（只请求一次）。"""
        if cls._suspension_cache is not None:
            return

        cls._suspension_cache = {}
        try:
            df = ak.stock_tfp_em()
        except Exception as e:
            logger.warning(f"获取停复牌数据失败，停牌标记将退化为 volume==0 推断: {e}")
            return
        finally:
            time.sleep(0.1)

        if df is None or df.empty:
            return

        df["停牌时间"] = pd.to_datetime(df["停牌时间"], errors="coerce")
        df["停牌截止时间"] = pd.to_datetime(df["停牌截止时间"], errors="coerce")

        for _, row in df.iterrows():
            code = str(row["代码"]).strip()
            susp_start = row["停牌时间"]
            susp_end = row["停牌截止时间"]

            if pd.isna(susp_start):
                continue

            start_d = susp_start.date()
            end_d = susp_end.date() if not pd.isna(susp_end) else None

            cls._suspension_cache.setdefault(code, []).append((start_d, end_d))

        total = sum(len(v) for v in cls._suspension_cache.values())
        logger.info(f"停复牌数据加载完成：{len(cls._suspension_cache)} 只股票，{total} 条记录")

    def _is_st_stock(self, symbol: str) -> bool:
        """判断 symbol 当前是否为 ST 股（基于 stock_zh_a_st_em 实时快照）。"""
        self._load_st_codes()
        code = self._to_ak_code(symbol)
        return code in self._st_codes_cache

    @classmethod
    def _load_st_codes(cls) -> None:
        """加载并缓存当前 ST 股代码集合（只请求一次）。"""
        if cls._st_codes_cache is not None:
            return

        cls._st_codes_cache = set()
        try:
            df = ak.stock_zh_a_st_em()
        except Exception as e:
            logger.warning(f"获取 ST 股列表失败，is_st 将全部设为 False: {e}")
            return
        finally:
            time.sleep(0.1)

        if df is None or df.empty:
            return

        code_col = "代码" if "代码" in df.columns else df.columns[1]
        cls._st_codes_cache = set(df[code_col].astype(str).str.strip())
        logger.info(f"ST 股数据加载完成：{len(cls._st_codes_cache)} 只 ST 股")

    # ── 私有方法 ────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_board(code: str) -> str:
        """根据股票代码前缀判断板块。"""
        if code.startswith("688") or code.startswith("689"):
            return "star"   # 科创板：±20% 涨跌停
        if code.startswith("300") or code.startswith("301"):
            return "gem"    # 创业板：±20%
        if code.startswith("4") or code.startswith("8"):
            return "bj"     # 北交所：±30%
        return "main"       # 主板：±10%

    @staticmethod
    def _normalize_bars(
        df: pd.DataFrame,
        suspended_dates: set[date] | None = None,
        is_st: bool = False,
    ) -> pd.DataFrame:
        """
        标准化 akshare stock_zh_a_hist 返回的日线数据。

        akshare 列名（East Money 源）：
          日期, 股票代码, 开盘, 收盘, 最高, 最低,
          成交量（手）, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
        """
        col_map = {
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",   # 单位：手（100股），下方转换
            "成交额": "amount",
            "成交金额": "amount",
            "涨跌额": "_price_change",
            "涨跌": "_price_change",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        # 日期
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

        # 价格/金额
        for col in ["open", "high", "low", "close", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 成交量：手 → 股（×100）
        df["volume"] = (
            pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0).astype(int) * 100
        )

        # pre_close = close - 涨跌额
        if "_price_change" in df.columns:
            df["_price_change"] = pd.to_numeric(df["_price_change"], errors="coerce").fillna(0.0)
            df["pre_close"] = (df["close"] - df["_price_change"]).round(3)
        else:
            df["pre_close"] = df["close"]

        # is_st：使用 stock_zh_a_st_em 获取的当前 ST 股列表标记
        # 限制：这是快照数据，无法反映历史 ST 状态变更
        # 如果该股票当前是 ST，整段历史都标记为 ST（保守策略，宁可多标不漏标）
        df["is_st"] = is_st

        # 停牌判断：优先使用 stock_tfp_em 显式数据，回退到 volume==0 推断
        volume_zero = (df["volume"] == 0) & (df["amount"].fillna(0) == 0)
        if suspended_dates:
            explicit_suspended = df["trade_date"].isin(suspended_dates)
            df["is_suspended"] = explicit_suspended | volume_zero
        else:
            df["is_suspended"] = volume_zero

        # 过滤空行
        df = df.dropna(subset=["open", "close"])

        return df[[
            "trade_date", "open", "high", "low", "close",
            "volume", "amount", "pre_close", "is_st", "is_suspended",
        ]].reset_index(drop=True)

    @staticmethod
    def _compute_adj_factors(
        raw_df: pd.DataFrame,
        qfq_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        从不复权和前复权数据计算 adj_factor。

        adj_factor[d] = raw_close[d] / qfq_close[d]

        与 PriceAdjuster 约定对齐：
          - 最新日期：adj_factor ≈ 1.0（无未来除权）
          - 历史除权前：adj_factor > 1.0（如 2-for-1 股权后为 2.0）

        只返回 adj_factor 发生变化的日期（稀疏格式），与 baostock 一致。
        末尾始终包含最新日期（用于 PriceAdjuster.apply_qfq 中的 latest_factor）。
        """
        # 对齐日期列（可能因版本不同有差异）
        raw_close = pd.to_numeric(raw_df["收盘"], errors="coerce").values
        qfq_close = pd.to_numeric(qfq_df["收盘"], errors="coerce").values
        dates = pd.to_datetime(raw_df["日期"]).dt.date.values

        n = len(dates)
        if n == 0:
            return pd.DataFrame()

        # 计算因子（避免除零）
        with np.errstate(divide="ignore", invalid="ignore"):
            factors = np.where(qfq_close > 0, raw_close / qfq_close, 1.0)
        factors = np.round(factors.astype(float), 6)

        # 无复权调整
        if np.all(np.abs(factors - 1.0) < 1e-6):
            return pd.DataFrame()

        # 只保留：(1) adj_factor ≠ 1.0 的日期 + (2) 最后一个日期（设置 latest_factor）
        mask = np.abs(factors - 1.0) > 1e-6
        mask[-1] = True  # 末尾日期（latest_factor）

        return pd.DataFrame({
            "trade_date": dates[mask],
            "adj_factor": factors[mask],
        }).sort_values("trade_date").reset_index(drop=True)
