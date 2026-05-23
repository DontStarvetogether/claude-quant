"""
事件定义。

事件通过 EventBus 流转，按优先级处理：
  MARKET_DATA (10) → SIGNAL (20) → ORDER (30) → FILL (40) → EOD (90)

所有事件使用 __slots__ + 自定义 __init__ 实现，避免 Python 3.13 dataclass
继承时默认参数排序问题。
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import IntEnum

from cq.core.models import Bar, Order, Signal, Trade

# ── 优先级 ───────────────────────────────────────────────────────────────────


class EventPriority(IntEnum):
    MARKET_DATA = 10
    SIGNAL = 20
    ORDER = 30
    FILL = 40
    EOD = 90


# ── 基类 ────────────────────────────────────────────────────────────────────


class Event:
    """事件基类。使用 __slots__ 避免 dataclass 继承的默认值排序问题。"""

    __slots__ = ("timestamp", "priority")

    def __init__(self, timestamp: datetime, priority: EventPriority) -> None:
        self.timestamp = timestamp
        self.priority = priority

    def __lt__(self, other: Event) -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.timestamp < other.timestamp

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self.timestamp == other.timestamp and self.priority == other.priority


# ── 具体事件 ─────────────────────────────────────────────────────────────────


class BarEvent(Event):
    __slots__ = ("bar",)

    def __init__(self, bar: Bar, timestamp: datetime | None = None) -> None:
        ts = timestamp or datetime.combine(bar.trade_date, time(9, 30))
        super().__init__(ts, EventPriority.MARKET_DATA)
        self.bar = bar


class SignalEvent(Event):
    __slots__ = ("signal",)

    def __init__(self, signal: Signal, timestamp: datetime | None = None) -> None:
        ts = timestamp or signal.created_at or datetime.now()
        super().__init__(ts, EventPriority.SIGNAL)
        self.signal = signal


class OrderEvent(Event):
    __slots__ = ("order",)

    def __init__(self, order: Order, timestamp: datetime | None = None) -> None:
        ts = timestamp or order.created_at or datetime.now()
        super().__init__(ts, EventPriority.ORDER)
        self.order = order


class FillEvent(Event):
    __slots__ = ("trade",)

    def __init__(self, trade: Trade, timestamp: datetime | None = None) -> None:
        ts = timestamp or trade.trade_time
        super().__init__(ts, EventPriority.FILL)
        self.trade = trade


class RejectEvent(Event):
    __slots__ = ("order_id", "reason")

    def __init__(
        self,
        order_id: str,
        reason: str,
        timestamp: datetime | None = None,
    ) -> None:
        ts = timestamp or datetime.now()
        super().__init__(ts, EventPriority.FILL)
        self.order_id = order_id
        self.reason = reason


class EndOfDayEvent(Event):
    __slots__ = ("trade_date",)

    def __init__(
        self, trade_date: date, timestamp: datetime | None = None
    ) -> None:
        ts = timestamp or datetime.combine(trade_date, time(15, 0))
        super().__init__(ts, EventPriority.EOD)
        self.trade_date = trade_date


# ── 类型别名 ─────────────────────────────────────────────────────────────────

AnyEvent = BarEvent | SignalEvent | OrderEvent | FillEvent | RejectEvent | EndOfDayEvent
