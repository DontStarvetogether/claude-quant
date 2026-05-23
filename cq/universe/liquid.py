"""Liquidity-screened dynamic A-share universes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from cq.universe.base import Universe, UniverseNotFoundError

ALL_A_LIQUID_ID = "all_a_liquid"


@dataclass(frozen=True)
class LiquidUniverseConfig:
    """Rules for selecting a practical liquid A-share universe."""

    lookback_days: int = 20
    min_listing_days: int = 120
    min_avg_amount: float = 50_000_000.0
    min_price: float = 1.0
    max_price: float = 10_000.0
    exclude_st: bool = True
    exclude_suspended: bool = True
    max_zero_volume_days: int = 0
    top_n: int | None = None

    def __post_init__(self) -> None:
        if self.lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        if self.min_listing_days <= 0:
            raise ValueError("min_listing_days must be positive")
        if self.min_avg_amount < 0:
            raise ValueError("min_avg_amount must be non-negative")
        if self.min_price < 0:
            raise ValueError("min_price must be non-negative")
        if self.max_price <= self.min_price:
            raise ValueError("max_price must be greater than min_price")
        if self.max_zero_volume_days < 0:
            raise ValueError("max_zero_volume_days must be non-negative")
        if self.top_n is not None and self.top_n <= 0:
            raise ValueError("top_n must be positive when provided")


@dataclass(frozen=True)
class LiquidUniverseSelection:
    """Selected symbols and per-symbol screening diagnostics."""

    symbols: tuple[str, ...]
    diagnostics: pd.DataFrame


class LiquidUniverseProvider:
    """Resolve the ALL_A_LIQUID dynamic universe from bar data."""

    def __init__(
        self,
        bars: pd.DataFrame,
        config: LiquidUniverseConfig | None = None,
        *,
        universe_id: str = ALL_A_LIQUID_ID,
    ) -> None:
        self._bars = bars.copy()
        self._config = config or LiquidUniverseConfig()
        self._universe_id = _normalize_universe_id(universe_id)

    def list_universes(self) -> list[Universe]:
        return [self.get_universe(self._universe_id)]

    def get_universe(self, universe_id: str) -> Universe:
        normalized = _normalize_universe_id(universe_id)
        if normalized != self._universe_id:
            raise UniverseNotFoundError(f"unknown universe: {universe_id}")
        return _build_liquid_universe(self._universe_id, self._config)

    def get_symbols(self, universe_id: str, trade_date: date | None = None) -> list[str]:
        self.get_universe(universe_id)
        if trade_date is None:
            raise ValueError("trade_date is required for dynamic liquidity universe")
        return build_all_a_liquid_universe(self._bars, trade_date, self._config)

    def select(self, trade_date: date) -> LiquidUniverseSelection:
        return select_all_a_liquid_universe(self._bars, trade_date, self._config)


class StoreBackedLiquidUniverseProvider:
    """Resolve ALL_A_LIQUID from a local data store."""

    def __init__(
        self,
        store: Any,
        config: LiquidUniverseConfig | None = None,
        *,
        candidate_symbols: list[str] | None = None,
        adjust: str = "qfq",
        history_calendar_days: int | None = None,
        universe_id: str = ALL_A_LIQUID_ID,
    ) -> None:
        self._store = store
        self._config = config or LiquidUniverseConfig()
        self._candidate_symbols = [symbol.upper() for symbol in candidate_symbols] if candidate_symbols else None
        self._adjust = adjust
        self._history_calendar_days = history_calendar_days or _default_history_calendar_days(
            self._config
        )
        self._universe_id = _normalize_universe_id(universe_id)

    def list_universes(self) -> list[Universe]:
        return [self.get_universe(self._universe_id)]

    def get_universe(self, universe_id: str) -> Universe:
        normalized = _normalize_universe_id(universe_id)
        if normalized != self._universe_id:
            raise UniverseNotFoundError(f"unknown universe: {universe_id}")
        return _build_liquid_universe(self._universe_id, self._config)

    def get_symbols(self, universe_id: str, trade_date: date | None = None) -> list[str]:
        return list(self.select_for_universe(universe_id, trade_date).symbols)

    def select_for_universe(
        self, universe_id: str, trade_date: date | None = None
    ) -> LiquidUniverseSelection:
        self.get_universe(universe_id)
        if trade_date is None:
            raise ValueError("trade_date is required for dynamic liquidity universe")
        return self.select(trade_date)

    def select(self, trade_date: date | str | pd.Timestamp) -> LiquidUniverseSelection:
        target_date = _to_date(trade_date)
        symbols = self._resolve_candidate_symbols()
        if not symbols:
            return select_all_a_liquid_universe(pd.DataFrame(), target_date, self._config)

        start_date = target_date - timedelta(days=self._history_calendar_days)
        bars = self._store.read_bars_batch(symbols, start_date, target_date, self._adjust)
        return select_all_a_liquid_universe(bars, target_date, self._config)

    def _resolve_candidate_symbols(self) -> list[str]:
        if self._candidate_symbols is not None:
            return list(self._candidate_symbols)
        if not hasattr(self._store, "list_symbols"):
            raise ValueError("candidate_symbols is required when store has no list_symbols()")
        return list(self._store.list_symbols(self._adjust))


def build_all_a_liquid_universe(
    bars: pd.DataFrame,
    trade_date: date | str | pd.Timestamp,
    config: LiquidUniverseConfig | None = None,
    **columns: str,
) -> list[str]:
    """Return symbols passing the ALL_A_LIQUID screen."""

    return list(select_all_a_liquid_universe(bars, trade_date, config, **columns).symbols)


def select_all_a_liquid_universe(
    bars: pd.DataFrame,
    trade_date: date | str | pd.Timestamp,
    config: LiquidUniverseConfig | None = None,
    *,
    date_col: str = "trade_date",
    symbol_col: str = "symbol",
    close_col: str = "close",
    amount_col: str = "amount",
    volume_col: str = "volume",
    is_st_col: str = "is_st",
    is_suspended_col: str = "is_suspended",
) -> LiquidUniverseSelection:
    """Screen a bar DataFrame into a dynamic liquid universe.

    The function is intentionally pure and in-memory. Store-backed providers can
    load a candidate universe first, then reuse this same rule engine.
    """

    config = config or LiquidUniverseConfig()
    target_date = _to_date(trade_date)
    diagnostics_columns = [
        "symbol",
        "selected",
        "reason",
        "latest_date",
        "trading_days",
        "lookback_rows",
        "avg_amount",
        "zero_volume_days",
        "latest_close",
        "is_st",
        "is_suspended",
    ]

    if bars.empty:
        return LiquidUniverseSelection(
            symbols=(),
            diagnostics=pd.DataFrame(columns=diagnostics_columns),
        )

    _require_columns(bars, [date_col, symbol_col, close_col, amount_col, volume_col])
    frame = bars.copy()
    frame["_screen_date"] = pd.to_datetime(frame[date_col]).dt.date
    frame["_screen_symbol"] = frame[symbol_col].astype(str).str.upper()
    frame["_screen_close"] = pd.to_numeric(frame[close_col], errors="coerce")
    frame["_screen_amount"] = pd.to_numeric(frame[amount_col], errors="coerce")
    frame["_screen_volume"] = pd.to_numeric(frame[volume_col], errors="coerce")

    if is_st_col not in frame.columns:
        frame[is_st_col] = False
    if is_suspended_col not in frame.columns:
        frame[is_suspended_col] = False

    frame = frame[frame["_screen_date"] <= target_date].copy()
    if frame.empty:
        return LiquidUniverseSelection(
            symbols=(),
            diagnostics=pd.DataFrame(columns=diagnostics_columns),
        )

    frame = (
        frame.sort_values(["_screen_symbol", "_screen_date"])
        .drop_duplicates(["_screen_symbol", "_screen_date"], keep="last")
        .reset_index(drop=True)
    )

    rows: list[dict[str, Any]] = []
    for symbol, group in frame.groupby("_screen_symbol", sort=True):
        ordered = group.sort_values("_screen_date")
        latest = ordered.iloc[-1]
        window = ordered.tail(config.lookback_days)

        latest_date = latest["_screen_date"]
        trading_days = int(ordered["_screen_date"].nunique())
        lookback_rows = int(len(window))
        avg_amount = float(window["_screen_amount"].mean()) if lookback_rows else 0.0
        zero_volume_days = int((window["_screen_volume"].fillna(0) <= 0).sum())
        latest_close = float(latest["_screen_close"]) if pd.notna(latest["_screen_close"]) else float("nan")
        is_st = _as_bool(latest[is_st_col])
        is_suspended = _as_bool(latest[is_suspended_col])

        selected = True
        reason = "selected"
        if latest_date != target_date:
            selected = False
            reason = "missing_latest_bar"
        elif trading_days < config.min_listing_days:
            selected = False
            reason = "insufficient_listing_days"
        elif lookback_rows < config.lookback_days:
            selected = False
            reason = "insufficient_lookback"
        elif config.exclude_st and is_st:
            selected = False
            reason = "st"
        elif config.exclude_suspended and is_suspended:
            selected = False
            reason = "suspended"
        elif pd.isna(latest_close) or not (config.min_price <= latest_close <= config.max_price):
            selected = False
            reason = "abnormal_price"
        elif pd.isna(avg_amount) or avg_amount < config.min_avg_amount:
            selected = False
            reason = "low_avg_amount"
        elif zero_volume_days > config.max_zero_volume_days:
            selected = False
            reason = "zero_volume"

        rows.append(
            {
                "symbol": symbol,
                "selected": selected,
                "reason": reason,
                "latest_date": latest_date,
                "trading_days": trading_days,
                "lookback_rows": lookback_rows,
                "avg_amount": avg_amount,
                "zero_volume_days": zero_volume_days,
                "latest_close": latest_close,
                "is_st": is_st,
                "is_suspended": is_suspended,
            }
        )

    diagnostics = pd.DataFrame(rows, columns=diagnostics_columns)
    selected = diagnostics[diagnostics["selected"]].sort_values(
        ["avg_amount", "symbol"], ascending=[False, True]
    )
    if config.top_n is not None:
        selected = selected.head(config.top_n)

    return LiquidUniverseSelection(
        symbols=tuple(selected["symbol"].tolist()),
        diagnostics=diagnostics.sort_values("symbol").reset_index(drop=True),
    )


def _normalize_universe_id(universe_id: str) -> str:
    return universe_id.removeprefix("preset_").lower()


def _build_liquid_universe(universe_id: str, config: LiquidUniverseConfig) -> Universe:
    return Universe(
        id=universe_id,
        name="全A流动性池",
        source="derived_from_bars",
        construction="dynamic_liquidity",
        description="按交易日从日线数据动态筛选的全A可交易流动性股票池。",
        symbols=(),
        metadata={"rules": asdict(config)},
    )


def _default_history_calendar_days(config: LiquidUniverseConfig) -> int:
    required_trading_days = max(config.min_listing_days, config.lookback_days)
    return max(365, required_trading_days * 3)


def _require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"bars missing required columns: {', '.join(missing)}")


def _to_date(value: date | str | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _as_bool(value: object) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)
