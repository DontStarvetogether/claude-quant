"""
HistoricalFeed：向引擎推送历史 Bar 数据。

内存策略：全量加载到 DataFrame，按 trade_date groupby 迭代。
提供 get_history() 供 StrategyContext 调用，使用预构建索引实现 O(log n) 查询。

复权模式：
  - qfq（默认）：加载预计算的前复权数据，所有价格基于最新因子一次性调整
  - dynamic：加载原始数据 + 复权因子，在 get_history() 时以当前日期为基准实时调整

停牌填充逻辑：
  1. 用交易日历 reindex，缺失行 = 停牌日
  2. OHLC 用 ffill（停牌前最后收盘价），volume/amount 填 0
  3. is_suspended 标记为 True
  4. IPO 前的日期不填充（直接丢弃）
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date
from typing import TYPE_CHECKING, Iterator, Optional

import pandas as pd
from loguru import logger

from cq.core.models import Bar
from cq.data.store.parquet_store import ParquetStore

if TYPE_CHECKING:
    from cq.data.calendar import TradingCalendar


class HistoricalFeed:
    """
    历史数据推送器。

    初始化时全量加载回测期间的数据到内存，
    然后按交易日顺序 yield (trade_date, list[Bar])。
    """

    def __init__(
        self,
        store: ParquetStore,
        symbols: list[str],
        start_date: date,
        end_date: date,
        calendar: Optional["TradingCalendar"] = None,
        adjust: str = "qfq",
    ) -> None:
        self._symbols = symbols
        self._start_date = start_date
        self._end_date = end_date
        self._adjust = adjust

        if adjust == "dynamic":
            logger.info(f"加载历史数据（动态复权）：{symbols}，{start_date} → {end_date}")
            self._df = store.read_bars_batch(symbols, start_date, end_date, adjust="raw")
            # 加载每只股票的复权因子，构建 trade_date → factor 的 Series
            self._sym_factors: dict[str, pd.Series] = {}
            for sym in symbols:
                adj = store.read_adj_factors(sym)
                if adj.empty:
                    continue
                sym_raw = self._df[self._df["symbol"] == sym]
                if sym_raw.empty:
                    continue
                merged = sym_raw[["trade_date"]].merge(
                    adj[["trade_date", "adj_factor"]], on="trade_date", how="left"
                )
                merged["adj_factor"] = merged["adj_factor"].ffill().bfill().fillna(1.0)
                self._sym_factors[sym] = merged.set_index("trade_date")["adj_factor"]
        else:
            logger.info(f"加载历史数据：{symbols}，{start_date} → {end_date}")
            self._df = store.read_bars_batch(symbols, start_date, end_date, adjust="qfq")
            self._sym_factors = {}

        if self._df.empty:
            logger.warning("无有效数据，请先下载")
        else:
            logger.info(f"加载完成：{len(self._df)} 条记录，{self._df['trade_date'].nunique()} 个交易日")

        # 停牌填充：用交易日历 reindex，补全缺失行
        if calendar is not None and not self._df.empty:
            self._df = self._fill_suspensions(self._df, symbols, start_date, end_date, calendar)

        # 构建每个 symbol 的历史索引（用于 get_history 快速查询，排除停牌日）
        self._history_index: dict[str, pd.DataFrame] = {}
        if not self._df.empty:
            for sym in symbols:
                sym_df = self._df[self._df["symbol"] == sym].drop(columns=["symbol"])
                if "is_suspended" in sym_df.columns:
                    sym_df = sym_df[~sym_df["is_suspended"].fillna(False).astype(bool)]
                sym_df = sym_df.sort_values("trade_date").reset_index(drop=True)
                self._history_index[sym] = sym_df

    @staticmethod
    def _fill_suspensions(
        df: pd.DataFrame,
        symbols: list[str],
        start_date: date,
        end_date: date,
        calendar: "TradingCalendar",
    ) -> pd.DataFrame:
        """
        对每只股票按交易日历 reindex，补全停牌缺失行。

        规则：
          - OHLC / pre_close / limit_up / limit_down：ffill（停牌前收盘价）
          - volume / amount：填 0
          - is_suspended：缺失行标记为 True
          - is_st：ffill（保持停牌前的 ST 状态）
          - IPO 前（该股票第一条记录之前）不填充，直接丢弃
        """
        all_trade_dates = calendar.trading_days_between(start_date, end_date)
        if not all_trade_dates:
            return df

        trade_date_index = pd.Index(all_trade_dates, name="trade_date")
        filled_parts: list[pd.DataFrame] = []
        total_filled = 0

        for sym in symbols:
            sym_df = df[df["symbol"] == sym].copy()
            if sym_df.empty:
                continue

            sym_df = sym_df.set_index("trade_date").sort_index()

            # IPO 日期 = 该股票第一条数据的日期
            ipo_date = sym_df.index.min()

            # 只 reindex IPO 日期之后的交易日（IPO 前不填充）
            valid_dates = trade_date_index[trade_date_index >= ipo_date]
            if valid_dates.empty:
                continue

            # 记录原始行数
            orig_count = len(sym_df)

            # reindex：缺失行 = 停牌日
            sym_df = sym_df.reindex(valid_dates)

            # 标记停牌：原本 is_suspended=True 的行 + reindex 产生的缺失行
            newly_missing = sym_df["close"].isna()
            if "is_suspended" in sym_df.columns:
                sym_df["is_suspended"] = sym_df["is_suspended"].fillna(False) | newly_missing
            else:
                sym_df["is_suspended"] = newly_missing

            filled_count = newly_missing.sum()
            if filled_count > 0:
                total_filled += filled_count
                if filled_count > 60:
                    logger.warning(f"{sym} 停牌填充 {filled_count} 天，超过 60 个交易日，请确认数据完整性")

                # OHLC + pre_close + limit_up/limit_down：ffill
                price_cols = ["open", "high", "low", "close", "pre_close"]
                for col in price_cols:
                    if col in sym_df.columns:
                        sym_df[col] = sym_df[col].ffill()

                # 涨跌停价：ffill（停牌期间保持停牌前的涨跌停价）
                for col in ["limit_up", "limit_down"]:
                    if col in sym_df.columns:
                        sym_df[col] = sym_df[col].ffill()

                # volume / amount：填 0
                for col in ["volume", "amount"]:
                    if col in sym_df.columns:
                        sym_df[col] = sym_df[col].fillna(0)

                # is_st：ffill（保持停牌前的 ST 状态）
                if "is_st" in sym_df.columns:
                    sym_df["is_st"] = sym_df["is_st"].ffill().fillna(False)

            # 恢复 symbol 列
            sym_df["symbol"] = sym
            sym_df = sym_df.reset_index()  # trade_date 回到列
            filled_parts.append(sym_df)

        if not filled_parts:
            return df

        result = (
            pd.concat(filled_parts, ignore_index=True)
            .sort_values(["trade_date", "symbol"])
            .reset_index(drop=True)
        )

        if total_filled > 0:
            logger.info(f"停牌填充完成：共补全 {total_filled} 条停牌记录")

        return result

    def iter_by_date(self) -> Iterator[tuple[date, list[Bar]]]:
        """
        按交易日顺序 yield (trade_date, bars)。
        同一日期内，bars 的顺序与 symbols 列表一致。
        包含停牌 bar（is_suspended=True），引擎用其更新持仓市值，
        撮合引擎会拒绝停牌日的订单。

        动态复权模式下，当天 Bar 的价格是真实市场价格（不复权）。
        """
        if self._df.empty:
            return

        for trade_date, group in self._df.groupby("trade_date", sort=True):
            bars = []
            for sym in self._symbols:
                row = group[group["symbol"] == sym]
                if not row.empty:
                    bars.append(self._row_to_bar(row.iloc[0], sym, trade_date))
            if bars:
                yield trade_date, bars

    def get_history(self, symbol: str, current_date: date, n: int) -> pd.DataFrame:
        """
        返回 symbol 在 current_date（含）之前最近 n 根 bar 的 DataFrame。

        列：trade_date, open, high, low, close, volume, amount
        按 trade_date 升序排列，最新 bar 在最后一行。
        注意：排除停牌日，只返回真实交易的 bar（保证指标计算准确）。

        动态复权模式下，价格以 current_date 的复权因子为基准实时调整。
        """
        sym_df = self._history_index.get(symbol)
        if sym_df is None or sym_df.empty:
            return pd.DataFrame()

        dates = sym_df["trade_date"].values
        # 找到 <= current_date 的最后一个索引
        end_idx = bisect_right(dates, current_date) - 1
        if end_idx < 0:
            return pd.DataFrame()

        start_idx = max(0, end_idx - n + 1)
        result = sym_df.iloc[start_idx : end_idx + 1]

        if self._adjust == "dynamic":
            result = self._apply_dynamic_adjustment(result, symbol, current_date)

        return result.reset_index(drop=True)

    @property
    def trade_dates(self) -> list[date]:
        """回测期间所有交易日列表（升序）。"""
        if self._df.empty:
            return []
        return sorted(self._df["trade_date"].unique().tolist())

    # ── 私有方法 ───────────────────────────────────────────────────────────────

    def _apply_dynamic_adjustment(
        self, df: pd.DataFrame, symbol: str, current_date: date
    ) -> pd.DataFrame:
        """以 current_date 的因子为基准，对历史价格做动态复权。"""
        factors = self._sym_factors.get(symbol)
        if factors is None:
            return df

        # 取 current_date 的因子
        if current_date in factors.index:
            current_factor = factors[current_date]
        else:
            valid = factors.index[factors.index <= current_date]
            current_factor = factors[valid[-1]] if len(valid) > 0 else 1.0

        # 映射每行的因子并计算比值
        row_factors = df["trade_date"].map(factors).ffill().bfill().fillna(1.0)
        ratio = current_factor / row_factors

        # 全部比值都为 1.0 时跳过计算
        if (ratio == 1.0).all():
            return df

        df = df.copy()
        for col in ["open", "high", "low", "close", "pre_close", "limit_up", "limit_down"]:
            if col in df.columns:
                df[col] = (df[col] * ratio).round(3)
        return df

    @staticmethod
    def _row_to_bar(row: pd.Series, symbol: str, trade_date: date) -> Bar:
        """将 DataFrame 行转换为 Bar 对象。"""
        is_st = bool(row.get("is_st", False))
        pct = 0.05 if is_st else 0.10  # ST 股 ±5%，普通股 ±10%
        close = float(row["close"])
        return Bar(
            symbol=symbol,
            trade_date=trade_date if isinstance(trade_date, date) else trade_date.date(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=close,
            volume=int(row["volume"]),
            amount=float(row["amount"]),
            limit_up=float(row.get("limit_up", round(close * (1 + pct), 2))),
            limit_down=float(row.get("limit_down", round(close * (1 - pct), 2))),
            pre_close=float(row.get("pre_close", row["close"])),
            is_st=is_st,
            is_suspended=bool(row.get("is_suspended", False)),
        )
