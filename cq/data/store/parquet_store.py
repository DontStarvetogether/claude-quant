"""
Parquet 本地存储。

目录结构：
  {data_root}/bars/{exchange}/{code}/raw.parquet
  {data_root}/bars/{exchange}/{code}/qfq.parquet
  {data_root}/bars/{exchange}/{code}/adj_factors.parquet
  {data_root}/calendar/{exchange}.parquet
  {data_root}/stock_info/all.parquet
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger

# Schema 版本，读取时校验
SCHEMA_VERSION = "1"


class ParquetStore:
    """
    Parquet 文件读写。线程不安全（回测单线程，下载时每个 symbol 独立路径无冲突）。
    """

    def __init__(self, data_root: Path) -> None:
        self._root = data_root
        self._root.mkdir(parents=True, exist_ok=True)

    # ── 路径计算 ───────────────────────────────────────────────────────────────

    def _bar_path(self, symbol: str, adjust: str) -> Path:
        """adjust: 'raw' | 'qfq'"""
        code, exchange = self._split_symbol(symbol)
        return self._root / "bars" / exchange / code / f"{adjust}.parquet"

    def _adj_path(self, symbol: str) -> Path:
        code, exchange = self._split_symbol(symbol)
        return self._root / "bars" / exchange / code / "adj_factors.parquet"

    def _calendar_path(self, exchange: str) -> Path:
        return self._root / "calendar" / f"{exchange}.parquet"

    def _stock_info_path(self) -> Path:
        return self._root / "stock_info" / "all.parquet"

    @staticmethod
    def _split_symbol(symbol: str) -> tuple[str, str]:
        """'600519.SH' → ('600519', 'SH')"""
        parts = symbol.split(".")
        if len(parts) != 2:
            raise ValueError(f"无效 symbol 格式: {symbol}（期望 '代码.交易所'）")
        return parts[0], parts[1].upper()

    # ── 日线数据 ───────────────────────────────────────────────────────────────

    def write_daily_bars(
        self,
        symbol: str,
        df: pd.DataFrame,
        adjust: str = "raw",
        mode: str = "append",
    ) -> None:
        """
        写入日线数据。

        mode='append'：与现有数据合并，按 trade_date 去重（新数据优先）。
        mode='overwrite'：直接覆盖。
        """
        path = self._bar_path(symbol, adjust)
        path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append" and path.exists():
            existing = pd.read_parquet(path)
            df = (
                pd.concat([existing, df])
                .sort_values("trade_date")
                .drop_duplicates(subset=["trade_date"], keep="last")
                .reset_index(drop=True)
            )

        metadata = {
            "cq_schema_version": SCHEMA_VERSION,
            "symbol": symbol,
            "adjust_type": adjust,
        }
        self._write_with_metadata(df, path, metadata)
        logger.debug(f"写入 {symbol} {adjust} {len(df)} 行 → {path}")

    def read_daily_bars(
        self,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """读取日线数据，可指定日期范围（pyarrow predicate pushdown）。"""
        path = self._bar_path(symbol, adjust)
        if not path.exists():
            logger.warning(f"本地无数据: {symbol} ({adjust})，请先下载")
            return pd.DataFrame()

        filters = self._date_filters(start_date, end_date)
        df = pd.read_parquet(path, filters=filters if filters else None)
        return df.sort_values("trade_date").reset_index(drop=True)

    def read_bars_batch(
        self,
        symbols: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """批量读取多只股票的日线数据，返回含 symbol 列的合并 DataFrame。"""
        dfs = []
        for sym in symbols:
            df = self.read_daily_bars(sym, start_date, end_date, adjust)
            if not df.empty:
                df.insert(0, "symbol", sym)
                dfs.append(df)

        if not dfs:
            return pd.DataFrame()

        return (
            pd.concat(dfs, ignore_index=True)
            .sort_values(["trade_date", "symbol"])
            .reset_index(drop=True)
        )

    def get_available_dates(
        self, symbol: str, adjust: str = "raw"
    ) -> tuple[date | None, date | None]:
        """返回本地数据的 (最早日期, 最新日期)，无数据返回 (None, None)。"""
        path = self._bar_path(symbol, adjust)
        if not path.exists():
            return None, None

        df = pd.read_parquet(path, columns=["trade_date"])
        if df.empty:
            return None, None

        dates = pd.to_datetime(df["trade_date"]).dt.date
        return dates.min(), dates.max()

    # ── 复权因子 ───────────────────────────────────────────────────────────────

    def write_adj_factors(
        self, symbol: str, df: pd.DataFrame, mode: str = "append"
    ) -> None:
        path = self._adj_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append" and path.exists():
            existing = pd.read_parquet(path)
            df = (
                pd.concat([existing, df])
                .sort_values("trade_date")
                .drop_duplicates(subset=["trade_date"], keep="last")
                .reset_index(drop=True)
            )

        metadata = {"cq_schema_version": SCHEMA_VERSION, "symbol": symbol}
        self._write_with_metadata(df, path, metadata)

    def read_adj_factors(self, symbol: str) -> pd.DataFrame:
        path = self._adj_path(symbol)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path).sort_values("trade_date").reset_index(drop=True)

    # ── 交易日历 ───────────────────────────────────────────────────────────────

    def write_calendar(self, exchange: str, dates: list[date]) -> None:
        path = self._calendar_path(exchange)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({"trade_date": sorted(dates)})
        metadata = {"cq_schema_version": SCHEMA_VERSION, "exchange": exchange}
        self._write_with_metadata(df, path, metadata)

    def read_calendar(self, exchange: str) -> list[date]:
        path = self._calendar_path(exchange)
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        return sorted(pd.to_datetime(df["trade_date"]).dt.date.tolist())

    # ── 股票基础信息 ─────────────────────────────────────────────────────────────

    def write_stock_info(self, df: pd.DataFrame) -> None:
        path = self._stock_info_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {"cq_schema_version": SCHEMA_VERSION}
        self._write_with_metadata(df, path, metadata)

    def read_stock_info(self) -> pd.DataFrame:
        path = self._stock_info_path()
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    # ── 工具方法 ───────────────────────────────────────────────────────────────

    @staticmethod
    def _write_with_metadata(df: pd.DataFrame, path: Path, metadata: dict) -> None:
        table = pa.Table.from_pandas(df, preserve_index=False)
        # 合并现有 schema metadata（pyarrow 会保留 pandas metadata）
        existing_meta = table.schema.metadata or {}
        updated_meta = {**existing_meta, **{k.encode(): v.encode() for k, v in metadata.items()}}
        table = table.replace_schema_metadata(updated_meta)
        pq.write_table(table, path)

    @staticmethod
    def _date_filters(
        start_date: date | None, end_date: date | None
    ) -> list[tuple]:
        filters = []
        if start_date:
            filters.append(("trade_date", ">=", start_date))
        if end_date:
            filters.append(("trade_date", "<=", end_date))
        return filters

    def symbol_exists(self, symbol: str, adjust: str = "qfq") -> bool:
        return self._bar_path(symbol, adjust).exists()

    def list_symbols(self, adjust: str = "qfq") -> list[str]:
        """List symbols that have local daily bar files for the requested adjustment."""
        bars_root = self._root / "bars"
        if not bars_root.exists():
            return []

        symbols: list[str] = []
        for exchange_dir in bars_root.iterdir():
            if not exchange_dir.is_dir():
                continue
            exchange = exchange_dir.name.upper()
            for code_dir in exchange_dir.iterdir():
                if code_dir.is_dir() and (code_dir / f"{adjust}.parquet").exists():
                    symbols.append(f"{code_dir.name}.{exchange}")
        return sorted(symbols)
