"""
QMTExecutor：迅投 QMT 实盘执行器。

职责：
- 接收 SignalEvent → 风控检查 → 计算股数 → 通过 QMT API 向券商下单
- QMT 成交回调 → FillEvent 放入 event_queue（线程安全）
- QMT 拒单回调 → RejectEvent 放入 event_queue（线程安全）

与 SimulatedExecutor 的区别：
- 订单不经过 BarMatchingEngine 撮合，而是直接发往券商
- 成交回调来自 QMT 网络线程，通过 event_queue 传回主线程
- LiveEngine 负责在主循环中排空 event_queue 并推入 EventBus

依赖：pip install xtquant（仅在 QMT 客户端环境内可用）
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


class QMTExecutor:
    """
    QMT 实盘执行器。

    线程安全说明：
        on_signal() 在主线程中调用。
        _on_trade() / _on_order_error() 在 QMT 网络线程中调用。
        两者通过 self.event_queue (queue.Queue) 安全传递事件，
        由 LiveEngine 主循环统一排空并推入 EventBus。
    """

    def __init__(
        self,
        bus: EventBus,
        portfolio: PortfolioManager,
        risk: PreTradeRisk,
        account_id: str,
        mini_qmt_dir: str,
        session_id: int = 1,
    ) -> None:
        try:
            from xtquant.xttrader import XtQuantTrader  # type: ignore[import-not-found]
            from xtquant import xtconstant              # type: ignore[import-not-found]
            self._trader = XtQuantTrader(mini_qmt_dir, session_id)
            self._xtconst = xtconstant
        except ImportError as exc:
            raise ImportError(
                "请在 QMT 客户端环境中安装 xtquant：pip install xtquant"
            ) from exc

        self._bus = bus
        self._portfolio = portfolio
        self._risk = risk
        self._account_id = account_id
        self._current_date: date = date.today()

        # 线程安全的事件队列（QMT 网络线程写，主线程读）
        self.event_queue: queue.Queue[FillEvent | RejectEvent] = queue.Queue()

        # qmt_order_id → (our_order_id, symbol, side)
        self._pending: dict[int, tuple[str, str, OrderSide]] = {}

    # ── 连接与同步 ────────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """连接 QMT，注册回调，订阅账户。必须在 on_signal() 前调用。"""
        rc = self._trader.connect()
        if rc != 0:
            raise RuntimeError(f"QMT 连接失败，返回码 {rc}（请确认 QMT 客户端已启动）")

        self._trader.register_callback(_QMTCallback(self))

        from xtquant.xttype import StockAccount  # type: ignore[import-not-found]
        self._account = StockAccount(self._account_id)
        self._trader.subscribe(self._account)
        logger.info(f"QMT 已连接，账户: {self._account_id}")

    def sync_positions(self) -> None:
        """
        从 QMT 同步持仓和资金到 PortfolioManager。

        建议在每日 before_trading() 前调用，保证本地账户状态与券商一致。
        """
        asset = self._trader.query_stock_asset(self._account)
        positions = self._trader.query_stock_positions(self._account)

        self._portfolio.sync_from_broker(
            cash=asset.cash,
            positions=[
                {
                    "symbol": p.stock_code,
                    "total_qty": p.volume,
                    "tradeable_qty": p.can_use_volume,
                    "avg_cost": p.open_price,
                    "last_price": (
                        p.market_value / p.volume if p.volume > 0 else 0.0
                    ),
                }
                for p in positions
            ],
        )
        logger.info(
            f"持仓同步完成：现金 {asset.cash:,.0f}  持仓 {len(positions)} 只"
        )

    def set_current_date(self, trade_date: date) -> None:
        self._current_date = trade_date

    # ── 信号处理（主线程调用）────────────────────────────────────────────────────

    def on_signal(self, event: SignalEvent) -> None:
        """处理 SignalEvent：风控 → 计算股数 → QMT 下单。"""
        signal = event.signal

        # 风控检查
        passed, reason, clamped = self._risk.check(signal)
        if not passed:
            self.event_queue.put(RejectEvent(order_id=signal.signal_id, reason=reason))
            logger.debug(f"信号被风控拒绝 {signal.symbol}: {reason}")
            return
        if clamped is not None:
            signal = clamped

        quantity = self._resolve_quantity(signal)
        if quantity <= 0:
            self.event_queue.put(RejectEvent(
                order_id=signal.signal_id,
                reason="计算后股数为零（资金不足或价格过高）",
            ))
            return

        our_order_id = new_order_id()

        direction = (
            self._xtconst.STOCK_BUY
            if signal.side == OrderSide.BUY
            else self._xtconst.STOCK_SELL
        )
        price_type = (
            self._xtconst.FIXED_PRICE
            if signal.order_type == OrderType.LIMIT
            else self._xtconst.LATEST_PRICE
        )
        price = signal.limit_price or 0.0

        qmt_order_id = self._trader.order_stock(
            account=self._account,
            stock_code=signal.symbol,
            order_type=direction,
            order_volume=quantity,
            price_type=price_type,
            price=price,
            strategy_name=signal.signal_id[:16],
            order_remark=our_order_id,
        )

        if qmt_order_id == -1:
            self.event_queue.put(
                RejectEvent(order_id=our_order_id, reason="QMT 下单接口返回 -1（下单失败）")
            )
            return

        self._pending[qmt_order_id] = (our_order_id, signal.symbol, signal.side)
        logger.info(
            f"下单 {our_order_id}  {signal.symbol}  "
            f"{signal.side.value}  {quantity}股  qmt_id={qmt_order_id}"
        )

    # ── QMT 回调（由 _QMTCallback 转发，在 QMT 网络线程中执行）──────────────────

    def _on_trade(self, trade_data) -> None:
        """QMT 成交回报 → FillEvent（放入线程安全队列）。"""
        qmt_order_id = trade_data.order_id
        info = self._pending.get(qmt_order_id)
        if info is None:
            logger.warning(f"收到未知订单的成交回报: qmt_order_id={qmt_order_id}")
            return

        our_order_id, symbol, side = info

        amount = float(trade_data.traded_amount)
        commission = amount * 0.0003  # 佣金估算（实际以券商结算单为准）
        stamp_tax = amount * 0.001 if side == OrderSide.SELL else 0.0

        trade = Trade(
            trade_id=new_trade_id(),
            order_id=our_order_id,
            symbol=symbol,
            side=side,
            quantity=int(trade_data.traded_volume),
            price=float(trade_data.traded_price),
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            trade_time=datetime.fromtimestamp(trade_data.traded_time / 1000),
            trade_date=self._current_date,
        )
        self.event_queue.put(FillEvent(trade=trade))
        logger.info(
            f"成交回报 {symbol}  {side.value}  "
            f"{trade.quantity}股 @{trade.price:.2f}"
        )

    def _on_order_error(self, error_data) -> None:
        """QMT 订单错误 → RejectEvent（放入线程安全队列）。"""
        qmt_order_id = error_data.order_id
        info = self._pending.pop(qmt_order_id, None)
        our_order_id = info[0] if info else str(qmt_order_id)

        reason = f"[{error_data.error_id}] {error_data.error_msg}"
        self.event_queue.put(RejectEvent(order_id=our_order_id, reason=reason))
        logger.warning(f"订单错误 {our_order_id}: {reason}")

    # ── 私有方法 ─────────────────────────────────────────────────────────────────

    def _resolve_quantity(self, signal: Signal) -> int:
        """将 percent/amount/quantity 解析为实际股数（100 整数倍）。"""
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
            price = signal.limit_price or self._portfolio.get_last_price(signal.symbol)
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


class _QMTCallback:
    """
    XtQuantTraderCallback 适配器。

    QMT 要求回调对象实现固定方法名；此类将调用转发到 QMTExecutor。
    所有方法均在 QMT 网络线程中执行，禁止直接操作 EventBus。
    """

    def __init__(self, executor: QMTExecutor) -> None:
        self._executor = executor

    def on_connected(self) -> None:
        logger.info("QMT 连接建立")

    def on_disconnected(self) -> None:
        logger.warning("QMT 连接断开，请检查网络或重启 QMT 终端")

    def on_stock_trade(self, trade) -> None:
        self._executor._on_trade(trade)

    def on_order_callback(self, order) -> None:
        # 委托状态变化（已报/已撤等），记录日志即可
        logger.debug(
            f"委托回报: order_id={order.order_id}  status={order.order_status}"
        )

    def on_order_error(self, order_error) -> None:
        self._executor._on_order_error(order_error)

    def on_account_status(self, status) -> None:
        logger.debug(f"账户状态: {status.account_id}  status={status.status}")
