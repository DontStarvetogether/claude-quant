"""
Strategy 抽象基类。

策略只通过两种方式与外界交互：
  读：self.ctx（StrategyContext，只读视图）
  写：self.buy() / self.sell()（发出 SignalEvent）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from cq.core.events import FillEvent, RejectEvent, SignalEvent
from cq.core.models import (
    OrderSide,
    OrderType,
    PositionSnapshot,
    Signal,
    new_signal_id,
)
from cq.utils.trading_rules import AStockRules

if TYPE_CHECKING:
    from cq.core.event_bus import EventBus
    from cq.data.feed.historical import HistoricalFeed
    from cq.engine.portfolio import PortfolioManager


class StrategyContext:
    """
    策略的只读视图。

    策略不直接操作账户，通过此类查询状态。
    """

    def __init__(
        self,
        portfolio: PortfolioManager,
        feed: HistoricalFeed,
    ) -> None:
        self._portfolio = portfolio
        self._feed = feed
        self._current_date: date | None = None

    def _set_date(self, trade_date: date) -> None:
        self._current_date = trade_date

    def get_position(self, symbol: str) -> PositionSnapshot | None:
        """返回持仓快照，无持仓时返回 None。"""
        return self._portfolio.get_position(symbol)

    def get_cash(self) -> float:
        """当前可用现金。"""
        return self._portfolio.get_cash()

    def get_total_assets(self) -> float:
        """总资产（现金 + 持仓市值）。"""
        return self._portfolio.get_total_assets()

    def get_bar_history(self, symbol: str, n: int) -> pd.DataFrame:
        """
        返回 symbol 在当前交易日（含）之前最近 n 根 bar。

        列：trade_date, open, high, low, close, volume, amount
        升序排列，最新 bar 在最后一行（iloc[-1]）。
        """
        if self._current_date is None:
            return pd.DataFrame()
        return self._feed.get_history(symbol, self._current_date, n)

    def get_trade_date(self) -> date | None:
        """当前处理的交易日期。"""
        return self._current_date


class Strategy(ABC):
    """
    策略抽象基类。

    子类必须设置 strategy_id，并实现 on_bar()。
    其他生命周期方法（on_init, before_trading, after_trading, on_order_update）
    均有默认空实现，按需覆盖。
    """

    strategy_id: str = ""

    def __init__(self) -> None:
        self._bus: EventBus | None = None
        self.ctx: StrategyContext | None = None
        self._configured_params: dict[str, Any] = {}

    def _setup(self, bus: EventBus, ctx: StrategyContext) -> None:
        """引擎调用，设置事件总线和上下文。"""
        self._bus = bus
        self.ctx = ctx

    def _apply_configured_params(self) -> None:
        """引擎在 on_init() 后调用，应用 Web/API 传入的参数。"""
        for key, value in self._configured_params.items():
            setattr(self, key, value)

    # ── 生命周期 ───────────────────────────────────────────────────────────────

    def on_init(self) -> None:
        """引擎 add_strategy 时调用，做参数初始化。"""
        return None

    def before_trading(self, trade_date: date) -> None:
        """当日 bar 推送前调用，可做日初准备。"""
        return None

    @abstractmethod
    def on_bar(self, bar: Bar) -> None:  # type: ignore[name-defined]  # noqa: F821
        """每根 Bar 触发，策略核心逻辑。"""

    def after_trading(self, trade_date: date) -> None:
        """当日所有 bar 处理完后调用，适合跨股票决策。"""
        return None

    def on_order_update(self, event: FillEvent | RejectEvent) -> None:
        """收到成交或拒绝回报。"""
        return None

    # ── 下单接口 ───────────────────────────────────────────────────────────────

    def buy(
        self,
        symbol: str,
        price: float | None = None,
        quantity: int | None = None,
        percent: float | None = None,
        amount: float | None = None,
    ) -> str | None:
        """
        发出买入信号。

        三选一：quantity（股数）/ percent（占总资产比例）/ amount（金额）。
        price=None 表示次日开盘市价。

        返回 signal_id，可用于后续追踪。
        如果参数无效（三者均未指定），返回 None。
        """
        if quantity is None and percent is None and amount is None:
            return None

        # 若指定 quantity，确保是 100 的整数倍
        if quantity is not None:
            quantity = AStockRules.round_to_lot(quantity)
            if quantity <= 0:
                return None

        order_type = OrderType.LIMIT if price is not None else OrderType.MARKET
        sig = Signal(
            signal_id=new_signal_id(),
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=order_type,
            quantity=quantity,
            percent=percent,
            amount=amount,
            limit_price=price,
            created_at=datetime.now(),
        )

        self._bus.put(SignalEvent(signal=sig))  # type: ignore[union-attr]
        return sig.signal_id

    def sell(
        self,
        symbol: str,
        price: float | None = None,
        quantity: int | None = None,
        percent: float | None = None,
    ) -> str | None:
        """
        发出卖出信号。

        quantity=None 且 percent=None 时，默认全部卖出（100%）。
        """
        if quantity is not None:
            quantity = AStockRules.round_to_lot(quantity)
            if quantity <= 0:
                return None

        order_type = OrderType.LIMIT if price is not None else OrderType.MARKET
        sig = Signal(
            signal_id=new_signal_id(),
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=order_type,
            quantity=quantity,
            percent=percent if percent is not None else (1.0 if quantity is None else None),
            limit_price=price,
            created_at=datetime.now(),
        )

        self._bus.put(SignalEvent(signal=sig))  # type: ignore[union-attr]
        return sig.signal_id
