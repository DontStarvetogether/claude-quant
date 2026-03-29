"""
PreTradeRisk：下单前风控检查。

在 SignalEvent → OrderEvent 转换前执行，不通过则直接发出 RejectEvent。
"""

from __future__ import annotations

from loguru import logger

from cq.core.events import RejectEvent, SignalEvent
from cq.core.models import OrderSide, Signal
from cq.engine.portfolio import PortfolioManager
from cq.utils.config import RiskConfig
from cq.utils.trading_rules import AStockRules


class PreTradeRisk:

    def __init__(self, portfolio: PortfolioManager, config: RiskConfig) -> None:
        self._portfolio = portfolio
        self._config = config

    def check(self, signal: Signal) -> tuple[bool, str]:
        """
        返回 (passed, reason)。
        passed=False 时 reason 描述拒绝原因。
        """
        if signal.side == OrderSide.BUY:
            return self._check_buy(signal)
        else:
            return self._check_sell(signal)

    def _check_buy(self, signal: Signal) -> tuple[bool, str]:
        total_assets = self._portfolio.get_total_assets()
        cash = self._portfolio.get_cash()
        pos = self._portfolio.get_position(signal.symbol)
        pos_value = pos.market_value if pos else 0.0
        
        # 计算本次买入金额（基于可用现金）
        buy_amount = self._calc_buy_amount(signal, cash + pos_value)
        
        if buy_amount <= 0:
            return False, "买入金额为零（资金不足或量 < 100 股）"
        
        # 单股仓位上限
        new_pct = (pos_value + buy_amount) / total_assets if total_assets > 0 else 1.0
        if new_pct > self._config.max_position_pct:
            return False, (
                f"单股仓位 {new_pct:.1%} 超过上限 {self._config.max_position_pct:.1%}"
            )
        
        # 现金够不够
        if cash < buy_amount:
            return False, f"可用现金 {cash:.0f} 不足，需要 {buy_amount:.0f}"
        
        # 买入后现金储备检查
        remaining = cash - buy_amount
        min_reserve = total_assets * self._config.min_cash_reserve
        if remaining < min_reserve:
            return False, (
                f"买入后现金 {remaining:.0f} 低于最低储备 {min_reserve:.0f}"
            )
        
        return True, ""

    def _check_sell(self, signal: Signal) -> tuple[bool, str]:
        pos = self._portfolio.get_position(signal.symbol)
        if pos is None:
            return False, f"无持仓: {signal.symbol}"

        sell_qty = self._calc_sell_qty(signal, pos.tradeable_qty)
        if sell_qty <= 0:
            return False, "卖出数量为零"

        if pos.tradeable_qty < sell_qty:
            return False, (
                f"T+1限制: 请求卖出 {sell_qty} 股，可卖 {pos.tradeable_qty} 股"
            )

        return True, ""

    def _calc_buy_amount(self, signal: Signal, total_assets: float) -> float:
        if signal.quantity is not None:
            price = signal.limit_price or self._portfolio.get_last_price(signal.symbol)
            if price is None or price <= 0:
                return 0.0
            return price * signal.quantity
        elif signal.percent is not None:
            return total_assets * signal.percent
        elif signal.amount is not None:
            return signal.amount
        return 0.0

    @staticmethod
    def _calc_sell_qty(signal: Signal, tradeable_qty: int) -> int:
        if signal.quantity is not None:
            return signal.quantity
        elif signal.percent is not None:
            return AStockRules.round_to_lot(tradeable_qty * signal.percent)
        return tradeable_qty  # 默认全卖
