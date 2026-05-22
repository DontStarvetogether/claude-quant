"""
PreTradeRisk：下单前风控检查。

在 SignalEvent → OrderEvent 转换前执行，不通过则直接发出 RejectEvent。
"""

from __future__ import annotations

from datetime import date

from loguru import logger

from cq.core.events import RejectEvent, SignalEvent
from cq.core.models import OrderSide, Signal
from cq.engine.portfolio import PortfolioManager
from cq.utils.config import EngineConfig, RiskConfig
from cq.utils.trading_rules import AStockRules


class PreTradeRisk:

    def __init__(
        self,
        portfolio: PortfolioManager,
        config: RiskConfig,
        engine_config: EngineConfig | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._config = config
        self._engine_config = engine_config or EngineConfig()
        self._reserved_cash: float = 0.0  # 当日已通过风控但尚未成交的买入金额
        self._daily_trade_count: int = 0   # 当日已通过风控的交易笔数
        self._high_water: float = max(portfolio.get_total_assets(), 0.0)
        self._drawdown_stopped: bool = False
        self._risk_events: list[dict] = []

    def reset_reserved(self) -> None:
        """每个交易日开始时重置预留资金和交易计数（由执行器调用）。"""
        self._reserved_cash = 0.0
        self._daily_trade_count = 0

    @property
    def events(self) -> list[dict]:
        """返回本次运行中触发过的结构化风控事件。"""
        return list(self._risk_events)

    def update_equity_state(self, trade_date: date) -> None:
        """用当前权益更新最大回撤止损状态。"""
        total_assets = self._portfolio.get_total_assets()
        if total_assets > self._high_water:
            self._high_water = total_assets

        stop = self._config.max_drawdown_stop
        if stop <= 0 or self._high_water <= 0 or self._drawdown_stopped:
            return

        drawdown = total_assets / self._high_water - 1
        if drawdown <= -stop:
            self._drawdown_stopped = True
            event = {
                "type": "max_drawdown_stop",
                "trade_date": str(trade_date),
                "drawdown": round(drawdown, 6),
                "threshold": round(stop, 6),
                "equity": round(total_assets, 2),
                "high_water": round(self._high_water, 2),
                "action": "stop_new_buys",
            }
            self._risk_events.append(event)
            logger.warning(
                f"最大回撤止损触发 {trade_date}: 回撤 {drawdown:.2%}，"
                f"阈值 {stop:.2%}，暂停新买入"
            )

    def check(self, signal: Signal) -> tuple[bool, str, Signal | None]:
        """
        返回 (passed, reason, clamped_signal)。
        clamped_signal 在买入仓位超限截断时非空（含调整后的 percent/amount），
        调用方需用其替换原 signal。
        """
        # 单日最大交易笔数
        max_trades = self._config.max_daily_trades
        if max_trades and self._daily_trade_count >= max_trades:
            return False, f"当日交易笔数已达上限 {max_trades}", None

        if signal.side == OrderSide.BUY:
            if self._drawdown_stopped:
                return False, (
                    f"最大回撤止损已触发（阈值 {self._config.max_drawdown_stop:.1%}），暂停新买入"
                ), None
            passed, reason, clamped = self._check_buy(signal)
        else:
            passed, reason, _ = self._check_sell(signal)
            clamped = None

        if passed:
            self._daily_trade_count += 1
        return passed, reason, clamped

    def _check_buy(self, signal: Signal) -> tuple[bool, str, Signal | None]:
        total_assets = self._portfolio.get_total_assets()
        cash = self._portfolio.get_cash()
        pos = self._portfolio.get_position(signal.symbol)
        pos_value = pos.market_value if pos else 0.0
        clamped_signal: Signal | None = None

        # 计算本次买入名义金额（不含费用，用于仓位上限）
        buy_amount = self._calc_buy_amount(signal, total_assets)

        if buy_amount <= 0:
            return False, "买入金额为零（资金不足或量 < 100 股）", None

        # 单股仓位上限：percent/amount 信号截断，quantity 信号拒绝
        max_allowed = total_assets * self._config.max_position_pct - pos_value
        if max_allowed <= 0:
            return False, (
                f"单股仓位已达上限 {self._config.max_position_pct:.1%}"
                f"（现有 {pos_value:,.0f}，上限 {total_assets * self._config.max_position_pct:,.0f}）"
            ), None
        if buy_amount > max_allowed:
            if signal.quantity is not None:
                return False, (
                    f"单股仓位将超过 {self._config.max_position_pct:.1%}"
                    f"（买入 {buy_amount:,.0f} + 现有 {pos_value:,.0f} > 上限 {total_assets * self._config.max_position_pct:,.0f}）"
                ), None
            # percent/amount 信号：创建截断后的新 Signal（原 Signal 是 frozen）
            buy_amount = max_allowed
            clamped_signal = self._clone_buy_signal(signal, buy_amount, total_assets)
            logger.debug(
                f"风控截断 {signal.symbol} 买入名义金额 → {buy_amount:,.0f}"
            )

        # 扣除当日已预留现金后的可用资金
        available_cash = cash - self._reserved_cash
        min_reserve = total_assets * self._config.min_cash_reserve
        cash_budget = available_cash - min_reserve
        buy_cost = self.estimate_buy_cost(buy_amount)
        if buy_cost > cash_budget:
            if signal.quantity is not None:
                return False, (
                    f"可用现金不足（现金 {cash:.0f} - 已预留 {self._reserved_cash:.0f} "
                    f"- 最低储备 {min_reserve:.0f} = {cash_budget:.0f}，"
                    f"含费用需要 {buy_cost:.0f}）"
                ), None

            affordable_amount = self.max_notional_for_cash(cash_budget)
            if affordable_amount <= 0:
                return False, (
                    f"可用现金不足（现金 {cash:.0f} - 已预留 {self._reserved_cash:.0f} "
                    f"- 最低储备 {min_reserve:.0f} = {cash_budget:.0f}）"
                ), None
            buy_amount = min(buy_amount, affordable_amount)
            buy_cost = self.estimate_buy_cost(buy_amount)
            clamped_signal = self._clone_buy_signal(signal, buy_amount, total_assets)
            logger.debug(
                f"风控按现金截断 {signal.symbol} 买入名义金额 → {buy_amount:,.0f}"
            )

        if available_cash < buy_cost:
            return False, (
                f"可用现金不足（现金 {cash:.0f} - 已预留 {self._reserved_cash:.0f} = "
                f"{available_cash:.0f}，含费用需要 {buy_cost:.0f}）"
            ), None

        # 买入后现金储备检查
        remaining = available_cash - buy_cost
        if remaining < min_reserve:
            return False, (
                f"买入后现金 {remaining:.0f} 低于最低储备 {min_reserve:.0f}"
            ), None

        # 通过风控，预留该笔含费用资金（防止同日其他信号双花）
        self._reserved_cash += buy_cost
        return True, "", clamped_signal

    def _check_sell(self, signal: Signal) -> tuple[bool, str, None]:
        pos = self._portfolio.get_position(signal.symbol)
        if pos is None:
            return False, f"无持仓: {signal.symbol}", None

        sell_qty = self._calc_sell_qty(signal, pos.tradeable_qty)
        if sell_qty <= 0:
            return False, "卖出数量为零", None

        if pos.tradeable_qty < sell_qty:
            return False, (
                f"T+1限制: 请求卖出 {sell_qty} 股，可卖 {pos.tradeable_qty} 股"
            ), None

        return True, "", None

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

    def estimate_buy_cost(self, notional: float) -> float:
        """估算买入总支出：成交金额 + 佣金（含最低佣金）。"""
        if notional <= 0:
            return 0.0
        commission = max(
            notional * self._engine_config.commission_rate,
            self._engine_config.min_commission,
        )
        return notional + commission

    def max_notional_for_cash(self, cash_budget: float) -> float:
        """返回给定现金预算下最多可承担的买入名义金额（含佣金约束）。"""
        if cash_budget <= 0:
            return 0.0

        rate = self._engine_config.commission_rate
        min_commission = self._engine_config.min_commission

        if rate <= 0:
            return max(0.0, cash_budget - min_commission)

        candidate = cash_budget / (1 + rate)
        if candidate * rate < min_commission:
            return max(0.0, cash_budget - min_commission)
        return max(0.0, candidate)

    def quantity_for_buy_budget(self, price: float, cash_budget: float) -> int:
        """按整手返回给定预算能买入的最大股数，预算包含佣金。"""
        if price <= 0 or cash_budget <= 0:
            return 0
        quantity = AStockRules.round_to_lot(cash_budget / price)
        while quantity > 0 and self.estimate_buy_cost(price * quantity) > cash_budget + 1e-6:
            quantity -= 100
        return quantity

    @staticmethod
    def _clone_buy_signal(signal: Signal, amount: float, total_assets: float) -> Signal:
        """用新的名义金额复制买入信号，保留原下单方式语义。"""
        if signal.amount is not None:
            amount_value = amount
            percent_value = None
        else:
            amount_value = None
            percent_value = amount / total_assets if total_assets > 0 else 0.0
        return Signal(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            order_type=signal.order_type,
            percent=percent_value,
            amount=amount_value,
            limit_price=signal.limit_price,
            created_at=signal.created_at,
        )

    @staticmethod
    def _calc_sell_qty(signal: Signal, tradeable_qty: int) -> int:
        if signal.quantity is not None:
            return signal.quantity
        elif signal.percent is not None:
            return AStockRules.round_to_lot(tradeable_qty * signal.percent)
        return tradeable_qty  # 默认全卖
