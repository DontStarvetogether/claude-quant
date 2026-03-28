"""
SimulatedExecutor：模拟执行器（回测专用）。

职责：将通过风控的 SignalEvent 转换为 OrderEvent。
计算具体的股数（percent/amount → shares），向下取整到 100 股。
"""

from __future__ import annotations

from datetime import date, datetime

from loguru import logger

from cq.core.event_bus import EventBus
from cq.core.events import OrderEvent, RejectEvent, SignalEvent
from cq.core.models import Order, OrderSide, OrderStatus, OrderType, Signal, new_order_id
from cq.engine.portfolio import PortfolioManager
from cq.risk.pre_trade import PreTradeRisk
from cq.utils.trading_rules import AStockRules


class SimulatedExecutor:
    """
    模拟执行器。

    收到 SignalEvent → 风控检查 → 计算股数 → 发出 OrderEvent。
    """

    def __init__(
        self,
        bus: EventBus,
        portfolio: PortfolioManager,
        risk: PreTradeRisk,
    ) -> None:
        self._bus = bus
        self._portfolio = portfolio
        self._risk = risk
        self._current_date: date | None = None

    def set_current_date(self, trade_date: date) -> None:
        """引擎在每个交易日开始时调用，设置当前处理日期。"""
        self._current_date = trade_date

    def on_signal(self, event: SignalEvent) -> None:
        """处理 SignalEvent：风控 → 股数计算 → 发出 OrderEvent。"""
        signal = event.signal

        # 风控检查
        passed, reason = self._risk.check(signal)
        if not passed:
            self._bus.put(RejectEvent(order_id=signal.signal_id, reason=reason))
            logger.debug(f"信号被风控拒绝 {signal.symbol}: {reason}")
            return

        # 计算具体股数
        quantity = self._resolve_quantity(signal)
        if quantity <= 0:
            self._bus.put(RejectEvent(
                order_id=signal.signal_id,
                reason="计算后股数为零（资金不足或价格过高）",
            ))
            return

        order = Order(
            order_id=new_order_id(),
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            order_type=signal.order_type,
            quantity=quantity,
            limit_price=signal.limit_price,
            trade_date=self._current_date or date.today(),
            status=OrderStatus.PENDING,
            created_at=event.timestamp,
        )

        self._bus.put(OrderEvent(order=order, timestamp=event.timestamp))
        logger.debug(
            f"发出订单 {order.order_id} {order.symbol} "
            f"{order.side.value} {order.quantity}股"
        )

    def _resolve_quantity(self, signal: Signal) -> int:
        """将 percent/amount/quantity 统一解析为实际股数（100的整数倍）。"""
        if signal.quantity is not None:
            return signal.quantity if AStockRules.is_valid_lot(signal.quantity) else 0

        total_assets = self._portfolio.get_total_assets()

        if signal.side == OrderSide.BUY:
            if signal.percent is not None:
                amount = total_assets * signal.percent
            elif signal.amount is not None:
                amount = signal.amount
            else:
                return 0

            # 需要知道价格才能算股数
            price = self._get_price(signal)
            if price is None or price <= 0:
                return 0

            # 向下取整到100股
            return AStockRules.round_to_lot(amount / price)

        else:  # SELL
            pos = self._portfolio.get_position(signal.symbol)
            if pos is None:
                return 0
            if signal.percent is not None:
                return AStockRules.round_to_lot(pos.tradeable_qty * signal.percent)
            return pos.tradeable_qty  # 默认全卖（tradeable_qty 本身已是100整数倍）

    def _get_price(self, signal: Signal) -> float | None:
        """获取用于计算股数的参考价格：限价 > 最新价。"""
        if signal.limit_price is not None:
            return signal.limit_price
        return self._portfolio.get_last_price(signal.symbol)
