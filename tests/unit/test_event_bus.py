"""单元测试：事件总线"""

import pytest
from datetime import date, datetime, time

from cq.core.event_bus import EventBus
from cq.core.events import BarEvent, EventPriority, SignalEvent
from cq.core.models import Bar, OrderSide, OrderType, Signal, new_signal_id


def make_bar(d: date = date(2024, 1, 2)) -> Bar:
    return Bar(
        symbol="600519.SH", trade_date=d,
        open=100.0, high=110.0, low=95.0, close=108.0,
        volume=1000, amount=100000.0,
        limit_up=110.0, limit_down=90.0, pre_close=100.0,
    )


def make_signal() -> Signal:
    return Signal(
        signal_id=new_signal_id(),
        symbol="600519.SH",
        side=OrderSide.BUY,
        percent=0.5,
    )


class TestEventBus:
    def test_basic_dispatch(self):
        bus = EventBus()
        received = []
        bus.subscribe(BarEvent, lambda e: received.append(e))

        bar = make_bar()
        bus.put(BarEvent(bar))
        bus.dispatch_all()

        assert len(received) == 1
        assert received[0].bar == bar

    def test_priority_order(self):
        """事件应按优先级处理：MARKET_DATA 先于 SIGNAL。"""
        bus = EventBus()
        order = []

        bus.subscribe(BarEvent, lambda e: order.append("bar"))
        bus.subscribe(SignalEvent, lambda e: order.append("signal"))

        # 故意先放 signal，再放 bar
        sig = make_signal()
        bus.put(SignalEvent(signal=sig))
        bus.put(BarEvent(bar=make_bar()))

        bus.dispatch_all()

        assert order == ["bar", "signal"], f"期望 ['bar', 'signal']，实际 {order}"

    def test_handler_produces_new_event(self):
        """handler 可以在处理过程中 put 新事件，新事件应被正确处理。"""
        bus = EventBus()
        received_signals = []

        def on_bar(event: BarEvent) -> None:
            # bar 处理时产生 signal
            sig = make_signal()
            bus.put(SignalEvent(signal=sig))

        bus.subscribe(BarEvent, on_bar)
        bus.subscribe(SignalEvent, lambda e: received_signals.append(e))

        bus.put(BarEvent(bar=make_bar()))
        bus.dispatch_all()

        assert len(received_signals) == 1

    def test_multiple_handlers_fifo(self):
        """同优先级多个 handler 按注册顺序调用。"""
        bus = EventBus()
        order = []

        bus.subscribe(BarEvent, lambda e: order.append("first"))
        bus.subscribe(BarEvent, lambda e: order.append("second"))

        bus.put(BarEvent(bar=make_bar()))
        bus.dispatch_all()

        assert order == ["first", "second"]

    def test_clear_queue(self):
        bus = EventBus()
        bus.put(BarEvent(bar=make_bar()))
        assert bus.size == 1
        bus.clear()
        assert bus.size == 0
