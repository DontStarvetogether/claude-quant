"""
BarMatchingEngine：基于日线的订单撮合引擎。

核心设计：
- D 日下单 → 放入 pending_orders[D]
- D+1 日 process_pending_orders(D+1) 时，用 D+1 的 bar 撮合 D 的订单
- 保证无前视偏差（Look-ahead Bias Free）
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time

from loguru import logger

from cq.core.event_bus import EventBus
from cq.core.events import FillEvent, OrderEvent, RejectEvent
from cq.core.models import (
    Bar,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Trade,
    new_trade_id,
)
from cq.data.calendar import TradingCalendar
from cq.engine.portfolio import PortfolioManager
from cq.utils.config import EngineConfig
from cq.utils.trading_rules import AStockRules


class BarMatchingEngine:
    """
    日线撮合引擎。

    process_pending_orders(today) 必须在 today 的 bar 推送前调用，
    以确保用 today 的价格撮合 yesterday 的订单（D+1 成交）。
    """

    def __init__(
        self,
        bus: EventBus,
        portfolio: PortfolioManager,
        calendar: TradingCalendar,
        config: EngineConfig,
    ) -> None:
        self._bus = bus
        self._portfolio = portfolio
        self._calendar = calendar
        self._config = config

        # 待处理订单：{下单交易日: [Order]}
        self._pending: dict[date, list[Order]] = defaultdict(list)
        # 当日 bar 缓存（每日 bar 推送时更新）
        self._current_bars: dict[str, Bar] = {}

    def on_bar(self, bar: Bar) -> None:
        """每日 bar 到达时更新价格缓存。"""
        self._current_bars[bar.symbol] = bar

    def on_order(self, event: OrderEvent) -> None:
        """将订单放入当日的 pending 队列，等待 D+1 撮合。"""
        order = event.order
        self._pending[order.trade_date].append(order)
        logger.debug(f"订单入队 {order.order_id} {order.symbol} {order.side.value} {order.quantity}股")

    def process_pending_orders(self, today: date) -> None:
        """
        在 today 的 bar 推送前调用。
        处理 yesterday（前一交易日）的所有挂单，用 today 的 bar 撮合。
        """
        try:
            yesterday = self._calendar.prev_trading_day(today)
        except ValueError:
            return  # 第一个交易日，无前一日

        pending = self._pending.pop(yesterday, [])
        if not pending:
            return

        logger.debug(f"撮合 {yesterday} 的 {len(pending)} 笔订单（用 {today} 价格）")

        for order in pending:
            bar = self._current_bars.get(order.symbol)
            if bar is None:
                self._reject(order, f"无行情数据: {order.symbol} ({today})")
                continue
            self._match(order, bar)

    # ── 撮合逻辑 ──────────────────────────────────────────────────────────────

    def _match(self, order: Order, bar: Bar) -> None:
        """用 today（D+1）的 bar 撮合 yesterday（D）的订单。"""
        if bar.is_suspended:
            self._reject(order, f"停牌: {order.symbol}")
            return

        if order.side == OrderSide.BUY:
            self._match_buy(order, bar)
        else:
            self._match_sell(order, bar)

    def _match_buy(self, order: Order, bar: Bar) -> None:
        fill_price = bar.open

        # 涨停开盘：无法买入
        if fill_price >= bar.limit_up:
            self._reject(order, f"涨停开盘({fill_price:.2f})，无法买入")
            return

        # 限价单：委托价低于开盘价，无法成交
        if order.order_type == OrderType.LIMIT and order.limit_price is not None:
            if order.limit_price < fill_price:
                self._reject(order, f"限价{order.limit_price:.2f} < 开盘{fill_price:.2f}")
                return
            fill_price = min(order.limit_price, fill_price)

        # 滑点：买入方向价格上浮
        fill_price = self._apply_slippage(fill_price, OrderSide.BUY, bar)

        quantity = self._resolve_buy_quantity(order, fill_price)
        if quantity <= 0:
            return

        quantity, capacity_limited, capacity_limit_qty = self._resolve_capacity_quantity(
            order, bar, quantity
        )
        if quantity <= 0:
            return

        self._fill(
            order,
            fill_price,
            bar,
            quantity=quantity,
            capacity_limited=capacity_limited,
            capacity_limit_qty=capacity_limit_qty,
        )

    def _match_sell(self, order: Order, bar: Bar) -> None:
        # T+1 最终检查（PreTradeRisk 已检查，这里是最后防线）
        pos = self._portfolio.get_position(order.symbol)
        tradeable = pos.tradeable_qty if pos else 0

        if tradeable < order.quantity:
            self._reject(order, f"T+1限制: 可卖{tradeable}股，请求卖{order.quantity}股")
            return

        fill_price = bar.open

        # 跌停封板：无法卖出（开盘即跌停且全天收于跌停）
        if fill_price <= bar.limit_down and bar.close <= bar.limit_down:
            self._reject(order, f"跌停封板({fill_price:.2f})，无法卖出")
            return

        # 限价单：委托价高于开盘价，以开盘价成交（对卖方有利）
        if order.order_type == OrderType.LIMIT and order.limit_price is not None:
            if order.limit_price > fill_price:
                self._reject(order, f"限价{order.limit_price:.2f} > 开盘{fill_price:.2f}")
                return

        # 滑点：卖出方向价格下浮
        fill_price = self._apply_slippage(fill_price, OrderSide.SELL, bar)
        quantity, capacity_limited, capacity_limit_qty = self._resolve_capacity_quantity(
            order, bar, order.quantity
        )
        if quantity <= 0:
            return
        self._fill(
            order,
            fill_price,
            bar,
            quantity=quantity,
            capacity_limited=capacity_limited,
            capacity_limit_qty=capacity_limit_qty,
        )

    def _apply_slippage(self, price: float, side: OrderSide, bar: Bar) -> float:
        """对成交价施加滑点，买入上浮、卖出下浮，不超过涨跌停价。"""
        slippage = self._config.slippage
        if slippage <= 0:
            return price
        if side == OrderSide.BUY:
            adj_price = round(price * (1 + slippage), 2)
            if adj_price > bar.limit_up:
                logger.debug(f"{bar.symbol} 滑点后价格 {adj_price:.2f} 超涨停 {bar.limit_up:.2f}，截断")
                return bar.limit_up
            return adj_price
        else:
            adj_price = round(price * (1 - slippage), 2)
            if adj_price < bar.limit_down:
                logger.debug(f"{bar.symbol} 滑点后价格 {adj_price:.2f} 低于跌停 {bar.limit_down:.2f}，截断")
                return bar.limit_down
            return adj_price

    def _resolve_buy_quantity(self, order: Order, price: float) -> int:
        """成交前按真实开盘价、滑点和佣金做现金保护。"""
        cash = self._portfolio.get_cash()
        requested = order.quantity
        required = self._buy_total_cost(price, requested)
        if required <= cash + 1e-6:
            return requested

        if not order.allow_partial_fill:
            self._reject(
                order,
                f"现金不足: 可用{cash:.2f}，买入{requested}股含费用需{required:.2f}",
            )
            return 0

        affordable = self._max_affordable_quantity(price, cash)
        if affordable <= 0:
            self._reject(
                order,
                f"现金不足: 可用{cash:.2f}，无法按整手买入（开盘{price:.2f}）",
            )
            return 0

        logger.info(
            f"买入缩量 {order.symbol}: 请求{requested}股，"
            f"可用现金{cash:.2f}，实际成交{affordable}股"
        )
        return affordable

    def _buy_total_cost(self, price: float, quantity: int) -> float:
        amount = price * quantity
        commission = max(
            amount * self._config.commission_rate,
            self._config.min_commission,
        )
        return amount + round(commission, 2)

    def _max_affordable_quantity(self, price: float, cash: float) -> int:
        if price <= 0 or cash <= 0:
            return 0
        quantity = AStockRules.round_to_lot(cash / price)
        while quantity > 0 and self._buy_total_cost(price, quantity) > cash + 1e-6:
            quantity -= 100
        return quantity

    def _resolve_capacity_quantity(
        self,
        order: Order,
        bar: Bar,
        quantity: int,
    ) -> tuple[int, bool, int | None]:
        """按日成交量参与率限制单笔成交量。"""
        if not self._config.enable_capacity_limit:
            return quantity, False, None

        participation = max(0.0, min(1.0, self._config.max_volume_participation))
        capacity_qty = AStockRules.round_to_lot(int(bar.volume * participation))
        if capacity_qty <= 0:
            self._reject(
                order,
                f"成交容量不足: 当日成交量{bar.volume}股，参与率{participation:.1%}，无法按整手成交",
            )
            return 0, False, capacity_qty

        if quantity <= capacity_qty:
            return quantity, False, capacity_qty

        if not order.allow_partial_fill:
            self._reject(
                order,
                f"成交容量不足: 请求{quantity}股，容量上限{capacity_qty}股",
            )
            return 0, False, capacity_qty

        logger.info(
            f"容量缩量 {order.symbol}: 请求{quantity}股，"
            f"容量上限{capacity_qty}股，实际成交{capacity_qty}股"
        )
        return capacity_qty, True, capacity_qty

    def _fill(
        self,
        order: Order,
        price: float,
        bar: Bar,
        quantity: int | None = None,
        capacity_limited: bool = False,
        capacity_limit_qty: int | None = None,
    ) -> None:
        fill_quantity = quantity if quantity is not None else order.quantity
        amount = price * fill_quantity
        commission = max(
            amount * self._config.commission_rate,
            self._config.min_commission,
        )
        stamp_tax = (
            amount * self._config.stamp_tax_rate
            if order.side == OrderSide.SELL
            else 0.0
        )

        trade = Trade(
            trade_id=new_trade_id(),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_quantity,
            price=price,
            amount=amount,
            commission=round(commission, 2),
            stamp_tax=round(stamp_tax, 2),
            trade_time=datetime.combine(bar.trade_date, time(9, 30)),
            trade_date=bar.trade_date,
            requested_quantity=order.quantity,
            capacity_limited=capacity_limited,
            capacity_limit_qty=capacity_limit_qty,
        )

        self._bus.put(FillEvent(trade=trade))
        logger.debug(
            f"成交 {trade.symbol} {order.side.value} {trade.quantity}股 "
            f"@{price:.2f}，手续费{trade.commission:.2f}"
        )

    def _reject(self, order: Order, reason: str) -> None:
        order.status = OrderStatus.REJECTED
        self._bus.put(RejectEvent(order_id=order.order_id, reason=reason))
        logger.info(f"拒绝订单 {order.order_id} {order.symbol}: {reason}")
