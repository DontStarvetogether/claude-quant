"""
DataPipeline：协调 DataSource + PriceAdjuster + ParquetStore。

主要功能：
1. 增量下载（只下载本地没有的日期范围）
2. 复权因子更新（除权后重算 qfq.parquet）
3. 交易日历同步
4. 涨跌停价补充
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Optional

import pandas as pd
from loguru import logger

from cq.data.adjust.adjuster import PriceAdjuster
from cq.data.calendar import TradingCalendar
from cq.data.source.base import DataSource
from cq.data.store.parquet_store import ParquetStore
from cq.utils.trading_rules import AStockRules


class DataPipeline:
    """协调数据下载、复权、存储的完整流程。"""

    def __init__(
        self,
        source: DataSource,
        store: ParquetStore,
        calendar: TradingCalendar,
    ) -> None:
        self._source = source
        self._store = store
        self._calendar = calendar
        self._adjuster = PriceAdjuster()

    def update_symbol(
        self,
        symbol: str,
        end_date: Optional[date] = None,
        start_date: Optional[date] = None,
        force: bool = False,
    ) -> int:
        """
        增量更新单只股票数据。

        逻辑：
          - force=True：从 start_date（或上市日）全量重下
          - 无本地数据：从 start_date（或上市日）下载至 end_date
          - 有本地数据：
              * 尾部增量：[local_max+1, end_date]
              * 头部回填：若 start_date < local_min，则补下 [start_date, local_min-1]

        返回：新增的 bar 数量（尾部 + 头部）。
        """
        if end_date is None:
            end_date = date.today()

        local_min, local_max = self._store.get_available_dates(symbol, adjust="raw")

        # ── 情况 1：无本地数据 或 强制重下 ─────────────────────────────────────
        if local_max is None or force:
            if start_date is not None:
                start = start_date
            else:
                try:
                    info = self._source.fetch_stock_info(symbol)
                    start = info.get("list_date", date(2000, 1, 1))
                except Exception as e:
                    logger.warning(f"{symbol} 获取上市日期失败，使用 2000-01-01: {e}")
                    start = date(2000, 1, 1)

            return self._download_range(symbol, start, end_date, recalc_qfq=force)

        # ── 情况 2：有本地数据 — 尾部增量 + 头部回填 ────────────────────────────
        total_new = 0

        # 尾部增量：[local_max+1, end_date]
        try:
            tail_start = self._calendar.next_trading_day(local_max)
        except ValueError:
            tail_start = None

        if tail_start is not None and tail_start <= end_date:
            # 尾部增量：跳过复权因子下载（短期内几乎不会有除权）
            total_new += self._download_range(symbol, tail_start, end_date, recalc_qfq=False, skip_adj=True)
        else:
            logger.info(f"{symbol} 尾部已是最新（本地: {local_max}, 目标: {end_date}）")

        # 头部回填：若请求的 start_date 早于本地最早日
        if start_date is not None and start_date < local_min:
            try:
                head_end = self._calendar.prev_trading_day(local_min)
            except (ValueError, AttributeError):
                # 若日历无 prev_trading_day 方法，用 local_min 前一天（让 DataSource 自行去重）
                from datetime import timedelta
                head_end = local_min - timedelta(days=1)

            if head_end >= start_date:
                logger.info(f"{symbol} 头部回填 {start_date} → {head_end}（当前本地最早: {local_min}）")
                # 头部数据会改变复权序列，需要重算 qfq
                total_new += self._download_range(symbol, start_date, head_end, recalc_qfq=True)

        if total_new == 0:
            logger.info(f"{symbol} 数据已是最新")

        return total_new

    def _download_range(
        self,
        symbol: str,
        start: date,
        end: date,
        recalc_qfq: bool = False,
        skip_adj: bool = False,
    ) -> int:
        """
        下载并存储 [start, end] 区间的数据（内部工具）。

        recalc_qfq=True：下载后强制重算整个 qfq 序列（用于头部回填或 force 重下）。
        skip_adj=True：跳过复权因子下载（增量尾部更新时使用，假设 adj_factor=1.0）。
        """
        if start > end:
            return 0

        logger.info(f"下载 {symbol} {start} → {end}")

        try:
            raw_df = self._source.fetch_daily_bars(symbol, start, end)
        except Exception as e:
            logger.error(f"{symbol} 下载失败: {e}")
            return 0

        if raw_df.empty:
            logger.warning(f"{symbol} 无数据（{start} → {end}）")
            return 0

        raw_df = self._fill_limit_prices(raw_df, symbol)
        self._store.write_daily_bars(symbol, raw_df, adjust="raw", mode="append")

        adj_df = pd.DataFrame()
        if not skip_adj:
            try:
                adj_df = self._source.fetch_adj_factors(symbol, start, end)
            except Exception as e:
                logger.warning(f"{symbol} 复权因子下载失败，使用 1.0: {e}")
                adj_df = pd.DataFrame()

            if not adj_df.empty:
                self._store.write_adj_factors(symbol, adj_df, mode="append")

        has_split = self._adjuster.detect_split_dates(adj_df) if not adj_df.empty else []
        if recalc_qfq or has_split:
            self._recalculate_qfq(symbol)
        else:
            self._append_qfq(symbol, raw_df, adj_df)

        new_count = len(raw_df)
        logger.info(f"{symbol} 新增 {new_count} 条记录")
        return new_count

    def update_batch(
        self,
        symbols: list[str],
        end_date: Optional[date] = None,
        start_date: Optional[date] = None,
        max_workers: int = 8,
        force: bool = False,
    ) -> dict[str, int]:
        """并行更新多只股票，返回 {symbol: 新增bar数}。"""
        results: dict[str, int] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.update_symbol, sym, end_date, start_date, force): sym
                for sym in symbols
            }
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    results[sym] = future.result()
                except Exception as e:
                    logger.error(f"{sym} 更新失败: {e}")
                    results[sym] = 0

        total = sum(results.values())
        logger.info(f"批量更新完成：{len(symbols)} 只股票，共新增 {total} 条记录")
        return results

    def sync_calendar(
        self,
        exchange: str = "SSE",
        years: Optional[list[int]] = None,
    ) -> None:
        """同步交易日历。"""
        if years is None:
            current_year = date.today().year
            years = list(range(2000, current_year + 1))

        all_dates: list[date] = []
        for year in years:
            try:
                days = self._source.fetch_trading_calendar(exchange, year)
                all_dates.extend(days)
                logger.debug(f"{exchange} {year} 日历：{len(days)} 个交易日")
            except Exception as e:
                logger.error(f"日历同步失败 {exchange} {year}: {e}")

        if all_dates:
            self._store.write_calendar(exchange, sorted(set(all_dates)))
            logger.info(f"{exchange} 日历更新完成：{len(all_dates)} 个交易日")

    # ── 私有方法 ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fill_limit_prices(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """根据 pre_close 和 is_st 补充涨跌停价。"""
        df = df.copy()
        limit_ups = []
        limit_downs = []

        for _, row in df.iterrows():
            pre_close = row["pre_close"]
            is_st = row.get("is_st", False)

            if pd.isna(pre_close) or pre_close <= 0:
                # 无法计算：用当日收盘价 ±涨跌幅 作为近似
                close = row.get("close", 0) or 0.0
                pct = AStockRules.LIMIT_PCT_ST if is_st else AStockRules.LIMIT_PCT_NORMAL
                limit_ups.append(round(close * (1 + pct), 2) if close > 0 else 0.0)
                limit_downs.append(round(close * (1 - pct), 2) if close > 0 else 0.0)
            else:
                lp = AStockRules.calc_limit_prices(float(pre_close), bool(is_st), symbol)
                limit_ups.append(lp.limit_up)
                limit_downs.append(lp.limit_down)

        df["limit_up"] = limit_ups
        df["limit_down"] = limit_downs
        return df

    def _recalculate_qfq(self, symbol: str) -> None:
        """重新计算整个历史序列的前复权价格。"""
        raw_df = self._store.read_daily_bars(symbol, adjust="raw")
        adj_df = self._store.read_adj_factors(symbol)

        if raw_df.empty:
            return

        qfq_df = self._adjuster.apply_qfq(raw_df, adj_df)
        # qfq 需要保留 limit_up/limit_down（使用原始价）
        for col in ["limit_up", "limit_down", "is_st", "is_suspended"]:
            if col in raw_df.columns:
                qfq_df[col] = raw_df[col].values

        self._store.write_daily_bars(symbol, qfq_df, adjust="qfq", mode="overwrite")
        logger.debug(f"{symbol} qfq 重算完成")

    def _append_qfq(
        self,
        symbol: str,
        new_raw_df: pd.DataFrame,
        adj_df: pd.DataFrame,
    ) -> None:
        """无除权时，只将新数据追加到 qfq（adj_factor = 1.0）。"""
        new_qfq = self._adjuster.apply_qfq(new_raw_df, adj_df)
        for col in ["limit_up", "limit_down", "is_st", "is_suspended"]:
            if col in new_raw_df.columns:
                new_qfq[col] = new_raw_df[col].values
        self._store.write_daily_bars(symbol, new_qfq, adjust="qfq", mode="append")
