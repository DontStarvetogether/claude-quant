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
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd
from loguru import logger

from cq.data.adjust.adjuster import PriceAdjuster
from cq.data.calendar import TradingCalendar
from cq.data.source.base import DataSource
from cq.data.store.parquet_store import ParquetStore
from cq.utils.trading_rules import AStockRules


@dataclass
class SymbolUpdateDiagnostic:
    """单个 symbol 的数据准备诊断。"""

    symbol: str
    status: str
    new_records: int
    used_cache: bool
    local_first_date: Optional[str]
    local_last_date: Optional[str]
    requested_start: str
    requested_end: str
    error: Optional[str] = None
    list_date: Optional[str] = None
    coverage_status: str = "unknown"
    source: Optional[str] = None
    cache_path: Optional[str] = None
    cache_updated_at: Optional[str] = None
    raw_first_date: Optional[str] = None
    raw_last_date: Optional[str] = None
    qfq_first_date: Optional[str] = None
    qfq_last_date: Optional[str] = None
    factor_first_date: Optional[str] = None
    factor_last_date: Optional[str] = None
    qfq_available: bool = False
    factor_available: bool = False
    st_status_source: str = "unavailable"
    limit_price_source: str = "exchange_or_calculated"
    repair_actions: list[str] = field(default_factory=list)
    quality_level: str = "unknown"
    data_quality: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict:
        return asdict(self)


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
        """增量更新单只股票数据，返回新增 bar 数量。"""
        return self.update_symbol_diagnostic(
            symbol=symbol,
            end_date=end_date,
            start_date=start_date,
            force=force,
        ).new_records

    def update_symbol_diagnostic(
        self,
        symbol: str,
        end_date: Optional[date] = None,
        start_date: Optional[date] = None,
        force: bool = False,
    ) -> SymbolUpdateDiagnostic:
        """
        增量更新单只股票数据，返回结构化诊断。

        逻辑：
          - force=True：从 start_date（或上市日）全量重下
          - 无本地数据：从 start_date（或上市日）下载至 end_date
          - 有本地数据：
              * 尾部增量：[local_max+1, end_date]
              * 头部回填：若 start_date < local_min，则补下 [start_date, local_min-1]

        status:
          - updated：新增了数据
          - cache_hit：本地缓存已覆盖目标区间
          - download_failed_cache_available：下载失败，但本地有缓存可继续
          - download_failed_no_cache：下载失败，且本地无可用缓存
          - empty_source：数据源返回空数据
        """
        if end_date is None:
            end_date = date.today()

        local_min, local_max = self._store.get_available_dates(symbol, adjust="raw")
        requested_start = start_date or local_min or date(2000, 1, 1)
        list_date = self._fetch_list_date(symbol)
        total_new = 0
        errors: list[str] = []
        empty_source = False

        # ── 情况 1：无本地数据 或 强制重下 ─────────────────────────────────────
        if local_max is None or force:
            if start_date is not None:
                start = start_date
            else:
                start = list_date or date(2000, 1, 1)

            result = self._download_range_diagnostic(symbol, start, end_date, recalc_qfq=force)
            total_new += result["new_records"]
            if result["status"] == "failed":
                errors.append(result["error"] or "下载失败")
            elif result["status"] == "empty":
                empty_source = True
        else:
            # ── 情况 2：有本地数据 — 尾部增量 + 头部回填 ────────────────────────
            # 尾部增量：[local_max+1, end_date]
            try:
                tail_start = self._calendar.next_trading_day(local_max)
            except (ValueError, AttributeError):
                tail_start = None

            if tail_start is not None and tail_start <= end_date:
                result = self._download_range_diagnostic(
                    symbol, tail_start, end_date, recalc_qfq=False
                )
                total_new += result["new_records"]
                if result["status"] == "failed":
                    errors.append(result["error"] or "尾部增量下载失败")
                elif result["status"] == "empty":
                    empty_source = True
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
                    result = self._download_range_diagnostic(
                        symbol, start_date, head_end, recalc_qfq=True
                    )
                    total_new += result["new_records"]
                    if result["status"] == "failed":
                        errors.append(result["error"] or "头部回填下载失败")
                    elif result["status"] == "empty":
                        empty_source = True

        final_min, final_max = self._store.get_available_dates(symbol, adjust="raw")
        used_cache = final_min is not None and final_max is not None
        cache_covers_request = (
            used_cache
            and final_min <= requested_start
            and final_max >= end_date
        )

        if total_new > 0:
            status = "updated"
        elif errors and used_cache:
            status = "download_failed_cache_available"
        elif errors:
            status = "download_failed_no_cache"
        elif empty_source and not used_cache:
            status = "empty_source"
        elif cache_covers_request:
            status = "cache_hit"
        elif used_cache:
            status = "download_failed_cache_available" if empty_source else "cache_hit"
        else:
            status = "empty_source"

        if total_new == 0:
            logger.info(f"{symbol} 数据已是最新")

        repair_actions: list[str] = []
        data_quality = self._inspect_local_data_quality(
            symbol, requested_start, end_date, list_date=list_date
        )
        qfq_repair_warnings = {"qfq_adjust_factor_missing", "qfq_price_scale_mismatch"}
        if qfq_repair_warnings.intersection(data_quality.get("warnings") or []):
            logger.warning(f"{symbol} qfq 缓存结构或价格尺度不一致，自动重算 qfq 缓存")
            self._recalculate_qfq(symbol)
            repair_actions.append("recalculate_qfq")
            data_quality = self._inspect_local_data_quality(
                symbol, requested_start, end_date, list_date=list_date
            )
        cache_meta = self._cache_lineage(symbol)
        return SymbolUpdateDiagnostic(
            symbol=symbol,
            status=status,
            new_records=total_new,
            used_cache=used_cache,
            local_first_date=str(final_min) if final_min else None,
            local_last_date=str(final_max) if final_max else None,
            requested_start=str(requested_start),
            requested_end=str(end_date),
            error="; ".join(errors) if errors else None,
            list_date=str(list_date) if list_date else None,
            coverage_status=str(data_quality.get("coverage_status", "unknown")),
            source=self._source.__class__.__name__,
            cache_path=cache_meta["cache_path"],
            cache_updated_at=cache_meta["cache_updated_at"],
            raw_first_date=cache_meta["raw_first_date"],
            raw_last_date=cache_meta["raw_last_date"],
            qfq_first_date=cache_meta["qfq_first_date"],
            qfq_last_date=cache_meta["qfq_last_date"],
            factor_first_date=cache_meta["factor_first_date"],
            factor_last_date=cache_meta["factor_last_date"],
            qfq_available=bool(data_quality.get("qfq_available", False)),
            factor_available=bool(data_quality.get("factor_available", False)),
            st_status_source=str(data_quality.get("st_status_source", "unavailable")),
            limit_price_source=str(data_quality.get("limit_price_source", "exchange_or_calculated")),
            repair_actions=repair_actions,
            quality_level=str(data_quality.get("quality_level", "unknown")),
            data_quality=data_quality,
        )

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
        skip_adj：兼容旧调用，当前为保证复权正确性不再跳过复权因子更新。
        """
        if start > end:
            return 0
        result = self._download_range_diagnostic(
            symbol=symbol,
            start=start,
            end=end,
            recalc_qfq=recalc_qfq,
            skip_adj=skip_adj,
        )
        return result["new_records"]

    def _download_range_diagnostic(
        self,
        symbol: str,
        start: date,
        end: date,
        recalc_qfq: bool = False,
        skip_adj: bool = False,
    ) -> dict:
        """下载并存储 [start, end] 区间的数据，返回局部诊断。"""
        if start > end:
            return {"status": "skipped", "new_records": 0, "error": None}

        logger.info(f"下载 {symbol} {start} → {end}")

        try:
            raw_df = self._source.fetch_daily_bars(symbol, start, end)
        except Exception as e:
            logger.error(f"{symbol} 下载失败: {e}")
            return {"status": "failed", "new_records": 0, "error": str(e)}

        if raw_df.empty:
            logger.warning(f"{symbol} 无数据（{start} → {end}）")
            return {"status": "empty", "new_records": 0, "error": None}

        raw_df = self._fill_limit_prices(raw_df, symbol)
        self._store.write_daily_bars(symbol, raw_df, adjust="raw", mode="append")

        if skip_adj:
            logger.debug(f"{symbol} skip_adj 已弃用：仍下载复权因子以保证 qfq 正确")

        try:
            adj_df = self._source.fetch_adj_factors(symbol, start, end)
        except Exception as e:
            logger.warning(f"{symbol} 复权因子下载失败，将用本地已有因子重算 qfq: {e}")
            adj_df = pd.DataFrame()

        if not adj_df.empty:
            self._store.write_adj_factors(symbol, adj_df, mode="append")

        # 每次写入原始数据后都用完整 raw + 完整 adj_factors 重算 qfq。
        # 这比尾部 append 慢，但能避免除权日、因子基准变化和部分因子下载导致的静默错价。
        self._recalculate_qfq(symbol)

        new_count = len(raw_df)
        logger.info(f"{symbol} 新增 {new_count} 条记录")
        return {"status": "updated", "new_records": new_count, "error": None}

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

    def update_batch_diagnostic(
        self,
        symbols: list[str],
        end_date: Optional[date] = None,
        start_date: Optional[date] = None,
        max_workers: int = 8,
        force: bool = False,
    ) -> dict[str, SymbolUpdateDiagnostic]:
        """并行更新多只股票，返回结构化诊断。"""
        results: dict[str, SymbolUpdateDiagnostic] = {}
        target_end = end_date or date.today()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.update_symbol_diagnostic, sym, target_end, start_date, force): sym
                for sym in symbols
            }
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    results[sym] = future.result()
                except Exception as e:
                    logger.error(f"{sym} 更新失败: {e}")
                    local_min, local_max = self._store.get_available_dates(sym, adjust="raw")
                    results[sym] = SymbolUpdateDiagnostic(
                        symbol=sym,
                        status="download_failed_cache_available" if local_max else "download_failed_no_cache",
                        new_records=0,
                        used_cache=local_max is not None,
                        local_first_date=str(local_min) if local_min else None,
                        local_last_date=str(local_max) if local_max else None,
                        requested_start=str(start_date or local_min or date(2000, 1, 1)),
                        requested_end=str(target_end),
                        error=str(e),
                    )

        total = sum(item.new_records for item in results.values())
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
        # qfq 的涨跌停价和昨收必须与 OHLC 使用同一复权尺度，撮合时才能同口径比较。
        # 非价格状态字段仍沿用 raw。
        for col in ["is_st", "is_suspended"]:
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
        for col in ["is_st", "is_suspended"]:
            if col in new_raw_df.columns:
                new_qfq[col] = new_raw_df[col].values
        self._store.write_daily_bars(symbol, new_qfq, adjust="qfq", mode="append")

    def _fetch_list_date(self, symbol: str) -> date | None:
        """获取上市日期，失败时返回 None，避免数据准备因辅助信息中断。"""
        try:
            info = self._source.fetch_stock_info(symbol)
            value = info.get("list_date")
            if value is None:
                return None
            return value if isinstance(value, date) else pd.to_datetime(value).date()
        except Exception as e:
            logger.warning(f"{symbol} 获取上市日期失败: {e}")
            return None

    def _inspect_local_data_quality(
        self,
        symbol: str,
        requested_start: date,
        requested_end: date,
        list_date: date | None = None,
    ) -> dict[str, Any]:
        """给结果页/API 使用的轻量本地数据质量摘要。"""
        raw_df = self._store.read_daily_bars(symbol, adjust="raw")
        qfq_df = self._store.read_daily_bars(symbol, adjust="qfq")
        warnings: list[str] = []

        if raw_df.empty:
            return {
                "status": "missing",
                "quality_level": "failed",
                "warnings": ["raw_empty"],
                "coverage_status": "missing",
                "list_date": str(list_date) if list_date else None,
                "qfq_available": False,
                "factor_available": not self._store.read_adj_factors(symbol).empty,
                "st_status_source": "unavailable",
                "limit_price_source": "unavailable",
            }

        dates = pd.to_datetime(raw_df["trade_date"]).dt.date
        local_first = dates.min()
        local_last = dates.max()
        coverage_status = "ok"
        if local_first > requested_start:
            if list_date is not None and list_date > requested_start and local_first >= list_date:
                coverage_status = "pre_listing_gap"
                warnings.append("pre_listing_gap")
            else:
                coverage_status = "start_missing"
                warnings.append("coverage_incomplete")
        if local_last < requested_end:
            coverage_status = "end_missing"
            warnings.append("coverage_incomplete")
        if raw_df["trade_date"].duplicated().any():
            warnings.append("duplicate_trade_date")
        if not dates.is_monotonic_increasing:
            warnings.append("trade_date_not_sorted")
        if "volume" in raw_df.columns and (pd.to_numeric(raw_df["volume"], errors="coerce").fillna(0) <= 0).any():
            warnings.append("zero_volume_days")
        if "open" in raw_df.columns and (pd.to_numeric(raw_df["open"], errors="coerce").fillna(0) <= 0).any():
            warnings.append("invalid_open_price")
        limit_price_source = "exchange_or_calculated"
        if {"limit_up", "limit_down"}.difference(raw_df.columns):
            warnings.append("limit_price_missing")
            limit_price_source = "unavailable"
        elif "pre_close" not in raw_df.columns or (pd.to_numeric(raw_df["pre_close"], errors="coerce").fillna(0) <= 0).any():
            warnings.append("limit_price_approximate")
            limit_price_source = "approximate"
        if qfq_df.empty:
            warnings.append("qfq_missing")
        elif "adj_factor" not in qfq_df.columns:
            warnings.append("qfq_adjust_factor_missing")
        elif self._qfq_price_scale_mismatch(raw_df, qfq_df):
            warnings.append("qfq_price_scale_mismatch")

        adj_df = self._store.read_adj_factors(symbol)
        if adj_df.empty:
            warnings.append("factor_missing")
        hard_warnings = [
            w for w in warnings
            if w
            not in {
                "pre_listing_gap",
                "zero_volume_days",
                "limit_price_approximate",
            }
        ]
        status = "ok" if not hard_warnings else "degraded"
        if hard_warnings:
            quality_level = "failed" if any(
                w in hard_warnings
                for w in {
                    "coverage_incomplete",
                    "duplicate_trade_date",
                    "trade_date_not_sorted",
                    "invalid_open_price",
                    "limit_price_missing",
                    "factor_missing",
                    "qfq_missing",
                    "qfq_adjust_factor_missing",
                    "qfq_price_scale_mismatch",
                }
            ) else "warning"
        elif warnings:
            quality_level = "warning"
        else:
            quality_level = "pass"
        return {
            "status": status,
            "quality_level": quality_level,
            "warnings": warnings,
            "coverage_status": coverage_status,
            "list_date": str(list_date) if list_date else None,
            "qfq_available": not qfq_df.empty,
            "factor_available": not adj_df.empty,
            "st_status_source": "unavailable",
            "limit_price_source": limit_price_source,
            "local_first_date": str(local_first),
            "local_last_date": str(local_last),
        }

    def _cache_lineage(self, symbol: str) -> dict[str, Any]:
        """返回本地缓存的文件路径、更新时间和各层数据覆盖范围。"""
        raw_path = self._store._bar_path(symbol, "raw")
        qfq_path = self._store._bar_path(symbol, "qfq")
        factor_path = self._store._adj_path(symbol)
        raw_min, raw_max = self._store.get_available_dates(symbol, adjust="raw")
        qfq_min, qfq_max = self._store.get_available_dates(symbol, adjust="qfq")
        factor_first, factor_last = self._factor_dates(symbol)
        existing = [p for p in [raw_path, qfq_path, factor_path] if p.exists()]
        updated_at = max((p.stat().st_mtime for p in existing), default=None)
        return {
            "cache_path": str(raw_path.parent) if raw_path.parent.exists() else None,
            "cache_updated_at": datetime.fromtimestamp(updated_at).isoformat() if updated_at else None,
            "raw_first_date": str(raw_min) if raw_min else None,
            "raw_last_date": str(raw_max) if raw_max else None,
            "qfq_first_date": str(qfq_min) if qfq_min else None,
            "qfq_last_date": str(qfq_max) if qfq_max else None,
            "factor_first_date": str(factor_first) if factor_first else None,
            "factor_last_date": str(factor_last) if factor_last else None,
        }

    def _factor_dates(self, symbol: str) -> tuple[date | None, date | None]:
        adj_df = self._store.read_adj_factors(symbol)
        if adj_df.empty or "trade_date" not in adj_df.columns:
            return None, None
        dates = pd.to_datetime(adj_df["trade_date"]).dt.date
        return dates.min(), dates.max()

    @staticmethod
    def _qfq_price_scale_mismatch(raw_df: pd.DataFrame, qfq_df: pd.DataFrame) -> bool:
        """检测 qfq 的昨收/涨跌停价是否仍停留在原始价尺度。"""
        compare_cols = ["pre_close", "limit_up", "limit_down"]
        if "adj_factor" not in qfq_df.columns:
            return True
        required_cols = ["trade_date", "adj_factor", *compare_cols]
        if any(col not in raw_df.columns for col in ["trade_date", *compare_cols]):
            return False
        if any(col not in qfq_df.columns for col in required_cols):
            return True

        merged = raw_df[["trade_date", *compare_cols]].merge(
            qfq_df[required_cols],
            on="trade_date",
            how="inner",
            suffixes=("_raw", "_qfq"),
        )
        if merged.empty:
            return False

        factors = pd.to_numeric(merged["adj_factor"], errors="coerce").fillna(1.0)
        adjusted_rows = (factors - 1.0).abs() > 1e-6
        if not adjusted_rows.any():
            return False

        checked = merged.loc[adjusted_rows].copy()
        factors = factors.loc[adjusted_rows]
        for col in compare_cols:
            raw_values = pd.to_numeric(checked[f"{col}_raw"], errors="coerce")
            qfq_values = pd.to_numeric(checked[f"{col}_qfq"], errors="coerce")
            expected = (raw_values * factors).round(3)
            delta = (qfq_values - expected).abs()
            tolerance = expected.abs().mul(1e-4).clip(lower=0.02)
            if ((raw_values.notna()) & (qfq_values.notna()) & (delta > tolerance)).any():
                return True
        return False
