"""
核心数据模型。

所有模型使用 dataclass（frozen=True 表示不可变快照，frozen=False 表示可变状态）。
Bar / Trade / Signal 是不可变的事实记录；Position / Account 是可变的运行时状态。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ── 枚举 ────────────────────────────────────────────────────────────────────


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"   # 市价单（次日开盘价）
    LIMIT = "LIMIT"     # 限价单


class OrderStatus(str, Enum):
    PENDING = "PENDING"     # 等待撮合
    FILLED = "FILLED"       # 已成交
    REJECTED = "REJECTED"   # 已拒绝
    CANCELLED = "CANCELLED" # 已撤单


# ── 行情 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Bar:
    """单根 K 线。所有价格字段必须处在同一价格尺度（默认 qfq，dynamic 模式为原始价）。"""

    symbol: str          # "600519.SH" 格式
    trade_date: date

    open: float
    high: float
    low: float
    close: float
    volume: int          # 股数
    amount: float        # 成交额（元）

    # 涨跌停价（与 open/high/low/close 保持同一价格尺度）
    limit_up: float
    limit_down: float

    pre_close: float     # 前收盘价（与 OHLC 保持同一价格尺度）
    is_st: bool = False
    is_suspended: bool = False

    @property
    def pct_change(self) -> float:
        """涨跌幅"""
        return (self.close - self.pre_close) / self.pre_close if self.pre_close > 0 else 0.0


# ── 信号 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Signal:
    """策略发出的交易意图，由 self.buy() / self.sell() 创建。"""

    signal_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET

    # 三选一（由 PreTradeRisk 解析为具体金额/数量）
    quantity: Optional[int] = None       # 指定股数（100 整数倍）
    percent: Optional[float] = None      # 占总资产比例
    amount: Optional[float] = None       # 指定金额（元）

    limit_price: Optional[float] = None  # 限价单专用
    created_at: Optional[datetime] = None


# ── 订单 ────────────────────────────────────────────────────────────────────


@dataclass
class Order:
    """经风控通过、等待撮合的订单。"""

    order_id: str
    signal_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int            # 明确的股数（100 整数倍）
    limit_price: Optional[float]
    trade_date: date         # 下单交易日（D 日），D+1 才撮合
    allow_partial_fill: bool = True  # 资金不足时是否允许按整手缩量成交
    status: OrderStatus = OrderStatus.PENDING
    created_at: Optional[datetime] = None


# ── 成交 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Trade:
    """成交记录（不可变事实）。"""

    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    amount: float            # price * quantity
    commission: float        # 佣金
    stamp_tax: float         # 印花税（卖出时）
    trade_time: datetime
    trade_date: date
    requested_quantity: Optional[int] = None
    capacity_limited: bool = False
    capacity_limit_qty: Optional[int] = None

    @property
    def net_amount(self) -> float:
        """买入：总支出；卖出：实际到账"""
        if self.side == OrderSide.BUY:
            return self.amount + self.commission
        return self.amount - self.commission - self.stamp_tax

    @property
    def fill_ratio(self) -> float:
        """实际成交数量 / 请求数量。"""
        requested = getattr(self, "requested_quantity", None) or self.quantity
        return self.quantity / requested if requested > 0 else 1.0


# ── 持仓 ────────────────────────────────────────────────────────────────────


@dataclass
class Position:
    """单只股票的持仓状态（可变）。"""

    symbol: str
    total_qty: int = 0           # 总持仓（含今日买入）
    tradeable_qty: int = 0       # 可卖数量（T+1 约束，当日买入不计入）
    today_bought_qty: int = 0    # 今日买入数量（EOD settle 时解锁）
    avg_cost: float = 0.0        # 持仓均价（含佣金）
    last_price: float = 0.0      # 最新价（用于估算市值）

    @property
    def market_value(self) -> float:
        return self.total_qty * self.last_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.last_price - self.avg_cost) * self.total_qty

    @property
    def unrealized_pnl_pct(self) -> float:
        return (self.last_price - self.avg_cost) / self.avg_cost if self.avg_cost > 0 else 0.0

    def snapshot(self) -> PositionSnapshot:
        return PositionSnapshot(
            symbol=self.symbol,
            total_qty=self.total_qty,
            tradeable_qty=self.tradeable_qty,
            avg_cost=self.avg_cost,
            last_price=self.last_price,
            market_value=self.market_value,
            unrealized_pnl=self.unrealized_pnl,
        )


@dataclass(frozen=True)
class PositionSnapshot:
    """持仓快照（不可变，供策略只读访问）。"""

    symbol: str
    total_qty: int
    tradeable_qty: int
    avg_cost: float
    last_price: float
    market_value: float
    unrealized_pnl: float

    @property
    def unrealized_pnl_pct(self) -> float:
        return self.unrealized_pnl / (self.avg_cost * self.total_qty) if self.avg_cost > 0 and self.total_qty > 0 else 0.0


# ── 账户 ────────────────────────────────────────────────────────────────────


@dataclass
class Account:
    """账户状态（可变）。"""

    initial_capital: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def total_assets(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    @property
    def total_market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    def snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            cash=self.cash,
            positions={sym: pos.snapshot() for sym, pos in self.positions.items()},
            total_assets=self.total_assets,
            total_market_value=self.total_market_value,
        )


@dataclass(frozen=True)
class AccountSnapshot:
    """账户快照（不可变，供绩效记录和策略查询）。"""

    cash: float
    positions: dict[str, PositionSnapshot]
    total_assets: float
    total_market_value: float


# ── 工厂函数 ─────────────────────────────────────────────────────────────────


def new_signal_id() -> str:
    return f"S{uuid.uuid4().hex[:12].upper()}"


def new_order_id() -> str:
    return f"O{uuid.uuid4().hex[:12].upper()}"


def new_trade_id() -> str:
    return f"T{uuid.uuid4().hex[:12].upper()}"
