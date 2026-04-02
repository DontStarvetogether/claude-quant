"""
PaperExecutor：纸上交易执行器（用于测试和 paper trade）。

与 QMTExecutor 接口完全相同（connect/sync_positions/set_current_date/on_signal/event_queue），
但不依赖 QMT，直接用最新价格模拟即时成交，将 FillEvent/RejectEvent 放入 event_queue。

与 SimulatedExecutor 的区别：
  - SimulatedExecutor：同步模式，直接 bus.put()，用于回测引擎
  - PaperExecutor    ：异步接口，走 event_queue，用于实盘引擎（LiveEngine）的纸上交易

典型用途：
  - 不连接 QMT，但跑完整 LiveEngine 主循环，验证策略逻辑
  - 与真实 QMT 行情结合，只看信号不真实下单（监控模式）
"""

from __future__ import annotations

import queue
from datetime import date, datetime
from typing import Optional

from loguru import logger

from cq.core.event_bus import EventBus
from cq.core.events import FillEvent, RejectEvent, SignalEvent
from cq.core.models import (
    OrderSide,
    OrderType,
    Signal,
    Trade,
    new_order_id,
    new_trade_id,
)
from cq.engine.portfolio import PortfolioManager
from cq.risk.pre_trade import PreTradeRisk
from cq.utils.trading_rules import AStockRules


class PaperExecutor:
    """
    纸上交易执行器。

    成交规则：
      - 市价单：以 portfolio 中该标的的最新价立即成交
      - 限价单：同样立即成交（纸上交易简化处理，不模拟排队）
      - 成交价格加入 0.01% 的模拟滑点

    线程安全：on_signal() 在主线程中调用，无后台线程，event_queue 仅作接口兼容。
    """

    SLIPPAGE = 0.0001   # 模拟滑点（0.01%）

    def __init__(
        self,
        bus: EventBus,
        portfolio: PortfolioManager,
        risk: PreTradeRisk,
    ) -> None:
        self._bus = bus
        self._portfolio = portfolio
        self._risk = risk
        self._current_date: date = date.today()

        # 与 QMTExecutor 相同的接口：LiveEngine 从此队列读取事件
        self.event_queue: queue.Queue[FillEvent | RejectEvent] = queue.Queue()

    # ── QMTExecutor 兼容接口 ──────────────────────────────────────────────────────

    def connect(self) -> None:
        logger.info("[PaperExecutor] 模拟连接成功（纸上交易模式，不会产生真实订单）")

    def sync_positions(self) -> None:
        """纸上交易模式下，持仓由 PortfolioManager 自行维护，无需外部同步。"""
        logger.info(
            f"[PaperExecutor] 持仓同步（本地）：现金 {self._portfolio.get_cash():,.0f}  "
            f"持仓 {len(self._portfolio.get_all_positions())} 只"
        )

    def set_current_date(self, trade_date: date) -> None:
        self._current_date = trade_date

    def on_signal(self, event: SignalEvent) -> None:
        """处理 SignalEvent：风控 → 计算股数 → 模拟成交 → event_queue。"""
        signal = event.signal

        # 风控
        passed, reason = self._risk.check(signal)
        if not passed:
            self.event_queue.put(RejectEvent(order_id=signal.signal_id, reason=reason))
            logger.debug(f"[PaperExecutor] 风控拒绝 {signal.symbol}: {reason}")
            return

        quantity = self._resolve_quantity(signal)
        if quantity <= 0:
            self.event_queue.put(RejectEvent(
                order_id=signal.signal_id,
                reason="计算后股数为零（资金不足或价格过高）",
            ))
            return

        # 确定成交价
        fill_price = self._get_fill_price(signal)
        if fill_price is None or fill_price <= 0:
            self.event_queue.put(RejectEvent(
                order_id=signal.signal_id,
                reason="无法获取参考价格（可能尚未收到行情）",
            ))
            return

        # 加滑点（买入价略高，卖出价略低）
        if signal.side == OrderSide.BUY:
            fill_price = round(fill_price * (1 + self.SLIPPAGE), 2)
        else:
            fill_price = round(fill_price * (1 - self.SLIPPAGE), 2)

        our_order_id = new_order_id()
        amount = fill_price * quantity
        commission = max(amount * 0.0003, 5.0)   # 万3，最低5元
        stamp_tax = amount * 0.001 if signal.side == OrderSide.SELL else 0.0

        trade = Trade(
            trade_id=new_trade_id(),
            order_id=our_order_id,
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            price=fill_price,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            trade_time=datetime.now(),
            trade_date=self._current_date,
        )
        self.event_queue.put(FillEvent(trade=trade))
        logger.info(
            f"[PaperExecutor] 模拟成交  {signal.symbol}  "
            f"{signal.side.value}  {quantity}股 @{fill_price:.2f}  "
            f"(佣金 {commission:.2f})"
        )

    # ── 私有方法 ─────────────────────────────────────────────────────────────────

    def _get_fill_price(self, signal: Signal) -> Optional[float]:
        """成交价优先级：限价 > 最新价。"""
        if signal.limit_price is not None:
            return signal.limit_price
        return self._portfolio.get_last_price(signal.symbol)

    def _resolve_quantity(self, signal: Signal) -> int:
        """与 QMTExecutor/_resolve_quantity 相同逻辑。"""
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
            price = self._get_fill_price(signal)
            if not price or price <= 0:
                return 0
            return AStockRules.round_to_lot(amount / price)
        else:
            pos = self._portfolio.get_position(signal.symbol)
            if pos is None:
                return 0
            if signal.percent is not None:
                return AStockRules.round_to_lot(pos.tradeable_qty * signal.percent)
            return pos.tradeable_qty
