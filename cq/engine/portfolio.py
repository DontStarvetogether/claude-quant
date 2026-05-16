"""
PortfolioManager：持仓和资金管理。

职责：
- 处理成交，更新持仓和现金
- T+1 结算（EOD 解锁当日买入）
- 提供只读快照供策略查询
"""

from __future__ import annotations

from datetime import date

from loguru import logger

from cq.core.events import EndOfDayEvent, FillEvent
from cq.core.models import (
    Account,
    AccountSnapshot,
    Bar,
    OrderSide,
    Position,
    PositionSnapshot,
)
from cq.utils.config import EngineConfig


class PortfolioManager:

    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._account = Account(
            initial_capital=config.initial_capital,
            cash=config.initial_capital,
        )
        # 所有曾出现的 symbol 的最新价（含无持仓的股票，供风控计算用）
        self._market_prices: dict[str, float] = {}

    # ── 事件处理 ───────────────────────────────────────────────────────────────

    def on_fill(self, event: FillEvent) -> None:
        trade = event.trade

        if trade.side == OrderSide.BUY:
            pos = self._get_or_create_position(trade.symbol)

            # 更新均价（加权平均，佣金摊入成本）
            old_cost = pos.avg_cost * pos.total_qty
            new_cost = trade.amount + trade.commission
            new_total_qty = pos.total_qty + trade.quantity

            if new_total_qty > 0:
                pos.avg_cost = (old_cost + new_cost) / new_total_qty
            pos.total_qty = new_total_qty
            pos.today_bought_qty += trade.quantity
            # tradeable_qty 不增加！T+1 约束：EOD settle 后才解锁

            # 用成交价作为 last_price（确保 market_value 正确）
            pos.last_price = trade.price

            self._account.cash -= (trade.amount + trade.commission)
            logger.debug(
                f"买入成交 {trade.symbol} {trade.quantity}股 @{trade.price:.2f}, "
                f"现金剩余 {self._account.cash:.0f}"
            )

        else:  # SELL
            pos = self._account.positions.get(trade.symbol)
            if pos is None:
                logger.error(f"卖出时无持仓: {trade.symbol}（应该被风控拦截）")
                return

            pos.total_qty -= trade.quantity
            pos.tradeable_qty -= trade.quantity

            net_proceeds = trade.amount - trade.commission - trade.stamp_tax
            self._account.cash += net_proceeds

            logger.debug(
                f"卖出成交 {trade.symbol} {trade.quantity}股 @{trade.price:.2f}, "
                f"净收入 {net_proceeds:.0f}"
            )

            # 持仓清零时删除
            if pos.total_qty <= 0:
                del self._account.positions[trade.symbol]

    def settle_eod(self, event: EndOfDayEvent) -> None:
        """
        每日收盘后结算：解锁当日买入（T+1 "解锁端"）。

        对应 on_fill BUY 分支的 "锁定端"：
        - 买入时：today_bought_qty += qty（tradeable_qty 不变）
        - EOD 后：tradeable_qty += today_bought_qty，today_bought_qty 清零
        """
        for pos in self._account.positions.values():
            pos.tradeable_qty += pos.today_bought_qty
            pos.today_bought_qty = 0

    def update_prices(self, bars: list[Bar]) -> None:
        """用当日收盘价更新持仓市值和全局价格缓存（供权益曲线记录和风控使用）。"""
        for bar in bars:
            self._market_prices[bar.symbol] = bar.close
            if bar.symbol in self._account.positions:
                self._account.positions[bar.symbol].last_price = bar.close

    # ── 查询接口（供 StrategyContext / Risk 只读访问）───────────────────────────

    def get_position(self, symbol: str) -> PositionSnapshot | None:
        pos = self._account.positions.get(symbol)
        return pos.snapshot() if pos else None

    def get_all_positions(self) -> dict[str, PositionSnapshot]:
        return {sym: pos.snapshot() for sym, pos in self._account.positions.items()}

    def get_cash(self) -> float:
        return self._account.cash

    def get_total_assets(self) -> float:
        return self._account.total_assets

    def get_last_price(self, symbol: str) -> float | None:
        """返回 symbol 的最新价。优先从持仓取，再从全局价格缓存取。"""
        pos = self._account.positions.get(symbol)
        if pos and pos.last_price > 0:
            return pos.last_price
        return self._market_prices.get(symbol)

    def sync_from_broker(
        self,
        cash: float,
        positions: list[dict],
    ) -> None:
        """
        从券商同步账户状态（实盘专用）。

        覆盖本地 cash 和持仓，以券商数据为准。
        positions 每项格式：
            {
                "symbol": str,
                "total_qty": int,
                "tradeable_qty": int,
                "avg_cost": float,
                "last_price": float,
            }
        """
        self._account.cash = cash
        self._account.positions.clear()
        for p in positions:
            symbol = p["symbol"]
            pos = Position(symbol=symbol)
            pos.total_qty = int(p["total_qty"])
            pos.tradeable_qty = int(p["tradeable_qty"])
            pos.avg_cost = float(p.get("avg_cost", 0.0))
            pos.last_price = float(p.get("last_price", 0.0))
            if pos.total_qty > 0:
                self._account.positions[symbol] = pos
                self._market_prices[symbol] = pos.last_price

    def snapshot(self) -> AccountSnapshot:
        """返回账户完整快照（不可变），供 PerformanceTracker 记录。"""
        return self._account.snapshot()

    # ── 私有方法 ───────────────────────────────────────────────────────────────

    def _get_or_create_position(self, symbol: str) -> Position:
        if symbol not in self._account.positions:
            self._account.positions[symbol] = Position(symbol=symbol)
        return self._account.positions[symbol]
