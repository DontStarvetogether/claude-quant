"""
同步事件总线（回测用）。

使用 heapq 优先队列，保证同一交易日内事件按 priority 顺序处理。
相同 priority 时按 timestamp 保持 FIFO 顺序。
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Callable

from cq.core.events import AnyEvent

# 订阅者类型：接收事件，不返回值
Handler = Callable[[AnyEvent], None]


class EventBus:
    """
    同步事件总线（单线程，用于回测）。

    用法：
        bus = EventBus()
        bus.subscribe(BarEvent, strategy.on_bar_event)
        bus.put(BarEvent(bar))
        bus.dispatch_all()   # 处理队列中所有事件
    """

    def __init__(self) -> None:
        self._queue: list[tuple[int, int, AnyEvent]] = []
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        self._counter = 0   # 用于同优先级 FIFO（heapq 需要可比较的第二键）

    def subscribe(self, event_type: type, handler: Handler) -> None:
        """注册事件处理器。同一类型可注册多个处理器，按注册顺序调用。"""
        self._handlers[event_type].append(handler)

    def put(self, event: AnyEvent) -> None:
        """将事件放入队列。"""
        # (priority, counter, event)：counter 保证同 priority 下 FIFO
        heapq.heappush(self._queue, (event.priority, self._counter, event))
        self._counter += 1

    def dispatch_all(self) -> None:
        """
        处理队列中所有事件，直到队列清空。

        注意：handler 可能在执行中向队列 put 新事件（如策略的 on_bar → SignalEvent），
        这些新事件会被正确地按优先级处理。
        """
        while self._queue:
            _, _, event = heapq.heappop(self._queue)
            event_type = type(event)
            for handler in self._handlers.get(event_type, []):
                handler(event)

    def clear(self) -> None:
        """清空队列（不清订阅）。"""
        self._queue.clear()
        self._counter = 0

    @property
    def size(self) -> int:
        return len(self._queue)
