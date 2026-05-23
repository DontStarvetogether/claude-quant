"""
绩效指标计算。

所有指标在回测结束后一次性计算，不在主循环中计算。
基于交易日（252天/年）而非自然日。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from cq.core.models import AccountSnapshot, OrderSide, Trade


@dataclass
class CompletedTrade:
    symbol: str
    quantity: int
    buy_date: date
    sell_date: date
    pnl: float       # 绝对盈亏（元，已扣费）
    pnl_pct: float   # 百分比盈亏
    hold_days: int


@dataclass
class MetricsResult:
    # 收益
    total_return: float
    annual_return: float
    # 风险
    max_drawdown: float
    volatility: float
    # 风险调整收益
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    # 交易统计
    total_trades: int
    win_rate: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    avg_hold_days: float
    # 费用
    total_fees: float
    # 结果
    final_value: float
    initial_value: float
    # 最大回撤区间
    max_drawdown_start: Optional[date]
    max_drawdown_end: Optional[date]
    # 基准对比
    excess_return: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0
    information_ratio: float = 0.0
    tracking_error: float = 0.0
    benchmark_return: float = 0.0
    benchmark_annual_return: float = 0.0
    fill_count: int = 0
    round_trip_count: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    avg_daily_turnover: float = 0.0
    annual_turnover: float = 0.0
    max_daily_turnover: float = 0.0
    buy_turnover: float = 0.0
    sell_turnover: float = 0.0
    total_turnover: float = 0.0
    gross_return: float = 0.0
    net_return: float = 0.0
    gross_annual_return: float = 0.0
    net_annual_return: float = 0.0
    total_slippage_cost: float = 0.0
    cost_drag: float = 0.0
    cost_to_nav: float = 0.0
    avg_position_count: float = 0.0
    max_position_count: int = 0
    min_position_count: int = 0
    avg_cash_ratio: float = 0.0
    average_exposure: float = 0.0
    max_single_position_weight: float = 0.0
    avg_top5_concentration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "max_drawdown": self.max_drawdown,
            "volatility": self.volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "total_trades": self.total_trades,
            "fill_count": self.fill_count,
            "round_trip_count": self.round_trip_count,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "avg_daily_turnover": self.avg_daily_turnover,
            "annual_turnover": self.annual_turnover,
            "max_daily_turnover": self.max_daily_turnover,
            "buy_turnover": self.buy_turnover,
            "sell_turnover": self.sell_turnover,
            "total_turnover": self.total_turnover,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "gross_annual_return": self.gross_annual_return,
            "net_annual_return": self.net_annual_return,
            "total_slippage_cost": self.total_slippage_cost,
            "cost_drag": self.cost_drag,
            "cost_to_nav": self.cost_to_nav,
            "avg_position_count": self.avg_position_count,
            "max_position_count": self.max_position_count,
            "min_position_count": self.min_position_count,
            "avg_cash_ratio": self.avg_cash_ratio,
            "average_exposure": self.average_exposure,
            "max_single_position_weight": self.max_single_position_weight,
            "avg_top5_concentration": self.avg_top5_concentration,
            "win_rate": self.win_rate,
            "avg_profit": self.avg_profit,
            "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor,
            "avg_hold_days": self.avg_hold_days,
            "total_fees": self.total_fees,
            "final_value": self.final_value,
            "initial_value": self.initial_value,
            "excess_return": self.excess_return,
            "alpha": self.alpha,
            "beta": self.beta,
            "information_ratio": self.information_ratio,
            "tracking_error": self.tracking_error,
            "benchmark_return": self.benchmark_return,
            "benchmark_annual_return": self.benchmark_annual_return,
        }

    def summary(self) -> str:
        dd_start = self.max_drawdown_start.strftime("%Y-%m-%d") if self.max_drawdown_start else "N/A"
        dd_end = self.max_drawdown_end.strftime("%Y-%m-%d") if self.max_drawdown_end else "N/A"
        pf = f"{self.profit_factor:.2f}" if self.profit_factor != float("inf") else "∞"

        return (
            f"\n{'═'*45}\n"
            f"收益概况\n"
            f"  总收益率          {self.total_return:+.2%}\n"
            f"  年化收益率        {self.annual_return:+.2%}\n"
            f"  最终净值          {self.final_value:,.0f} 元\n"
            f"\n风险指标\n"
            f"  最大回撤          {self.max_drawdown:.2%}  ({dd_start} → {dd_end})\n"
            f"  年化波动率        {self.volatility:.2%}\n"
            f"  夏普比率          {self.sharpe_ratio:.4f}\n"
            f"  索提诺比率        {self.sortino_ratio:.4f}\n"
            f"  卡玛比率          {self.calmar_ratio:.4f}\n"
            f"\n交易统计\n"
            f"  总交易次数        {self.total_trades} 笔\n"
            f"  胜率              {self.win_rate:.2%}\n"
            f"  平均盈利          {self.avg_profit:+.2%}\n"
            f"  平均亏损          {self.avg_loss:+.2%}\n"
            f"  盈亏比            {pf}\n"
            f"  平均持仓          {self.avg_hold_days:.1f} 天\n"
            f"\n费用\n"
            f"  总手续费+印花税   {self.total_fees:,.0f} 元\n"
            f"{'═'*45}"
        )


class PerformanceMetrics:

    RF_ANNUAL = 0.03
    TRADING_DAYS = 252

    def compute(
        self,
        equity_curve: pd.Series,
        trades: list[Trade],
        account_history: dict[date, AccountSnapshot] | None = None,
    ) -> MetricsResult:
        """
        计算完整绩效指标。

        equity_curve：index=date, values=净资产（每日 EOD settle 后记录一次）
        trades：完整成交记录列表
        """
        if len(equity_curve) < 2:
            return self._empty_result(equity_curve.iloc[0] if len(equity_curve) == 1 else 0.0)

        rf_daily = self.RF_ANNUAL / self.TRADING_DAYS
        daily_returns = equity_curve.pct_change().dropna()

        n_days = len(equity_curve) - 1
        initial = equity_curve.iloc[0]
        final = equity_curve.iloc[-1]

        # 收益率
        total_return = (final - initial) / initial
        annual_return = (
            (1 + total_return) ** (self.TRADING_DAYS / n_days) - 1
            if n_days > 0 else 0.0
        )

        # 最大回撤
        rolling_max = equity_curve.cummax()
        drawdown = (equity_curve - rolling_max) / rolling_max
        max_drawdown = float(drawdown.min())
        dd_end = drawdown.idxmin()
        dd_start = equity_curve.loc[:dd_end].idxmax()

        # 夏普
        excess = daily_returns - rf_daily
        sharpe = (
            float(excess.mean() / excess.std() * np.sqrt(self.TRADING_DAYS))
            if excess.std() > 1e-10 else 0.0
        )

        # 索提诺（只用下行波动率）
        downside = excess[excess < 0]
        downside_std = float(np.sqrt((downside ** 2).mean())) if len(downside) > 0 else 1e-10
        sortino = float(excess.mean() / downside_std * np.sqrt(self.TRADING_DAYS))

        # 卡玛
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        # 波动率
        volatility = float(daily_returns.std() * np.sqrt(self.TRADING_DAYS))

        # 交易统计
        completed = self._pair_trades(trades)
        trade_stats = self._summarize_trades(completed)
        realized_pnl = trade_stats["realized_pnl"]
        unrealized_pnl = (final - initial) - realized_pnl

        # 费用
        total_fees = sum(t.commission + t.stamp_tax for t in trades)
        avg_assets = float(equity_curve.mean()) if len(equity_curve) else float(initial)
        turnover_stats = self._turnover_stats(equity_curve, trades, avg_assets)
        exposure_stats = self._exposure_stats(account_history)
        cost_drag = total_fees / initial if initial > 0 else 0.0
        gross_return = total_return + cost_drag
        gross_annual_return = (
            (1 + gross_return) ** (self.TRADING_DAYS / n_days) - 1
            if n_days > 0 and gross_return > -1
            else 0.0
        )

        return MetricsResult(
            total_return=round(total_return, 6),
            annual_return=round(annual_return, 6),
            max_drawdown=round(max_drawdown, 6),
            volatility=round(volatility, 6),
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            calmar_ratio=round(calmar, 4),
            total_trades=trade_stats["total_trades"],
            fill_count=len(trades),
            round_trip_count=trade_stats["total_trades"],
            realized_pnl=round(realized_pnl, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            avg_daily_turnover=round(turnover_stats["avg_daily_turnover"], 6),
            annual_turnover=round(turnover_stats["annual_turnover"], 6),
            max_daily_turnover=round(turnover_stats["max_daily_turnover"], 6),
            buy_turnover=round(turnover_stats["buy_turnover"], 6),
            sell_turnover=round(turnover_stats["sell_turnover"], 6),
            total_turnover=round(turnover_stats["total_turnover"], 6),
            gross_return=round(gross_return, 6),
            net_return=round(total_return, 6),
            gross_annual_return=round(gross_annual_return, 6),
            net_annual_return=round(annual_return, 6),
            total_slippage_cost=0.0,
            cost_drag=round(cost_drag, 6),
            cost_to_nav=round(total_fees / avg_assets, 6) if avg_assets > 0 else 0.0,
            avg_position_count=round(exposure_stats["avg_position_count"], 6),
            max_position_count=int(exposure_stats["max_position_count"]),
            min_position_count=int(exposure_stats["min_position_count"]),
            avg_cash_ratio=round(exposure_stats["avg_cash_ratio"], 6),
            average_exposure=round(exposure_stats["average_exposure"], 6),
            max_single_position_weight=round(exposure_stats["max_single_position_weight"], 6),
            avg_top5_concentration=round(exposure_stats["avg_top5_concentration"], 6),
            win_rate=round(trade_stats["win_rate"], 4),
            avg_profit=round(trade_stats["avg_profit"], 6),
            avg_loss=round(trade_stats["avg_loss"], 6),
            profit_factor=trade_stats["profit_factor"],
            avg_hold_days=round(trade_stats["avg_hold_days"], 1),
            total_fees=round(total_fees, 2),
            final_value=round(final, 2),
            initial_value=round(initial, 2),
            max_drawdown_start=dd_start if isinstance(dd_start, date) else dd_start.date() if hasattr(dd_start, 'date') else None,
            max_drawdown_end=dd_end if isinstance(dd_end, date) else dd_end.date() if hasattr(dd_end, 'date') else None,
        )

    @classmethod
    def _turnover_stats(
        cls,
        equity_curve: pd.Series,
        trades: list[Trade],
        avg_assets: float,
    ) -> dict[str, float]:
        """按日成交额 / 当日 EOD 净值计算换手率。"""
        if equity_curve.empty:
            return {
                "avg_daily_turnover": 0.0,
                "annual_turnover": 0.0,
                "max_daily_turnover": 0.0,
                "buy_turnover": 0.0,
                "sell_turnover": 0.0,
                "total_turnover": 0.0,
            }

        amount_by_date: dict[date, float] = defaultdict(float)
        buy_amount = 0.0
        sell_amount = 0.0
        for trade in trades:
            amount_by_date[trade.trade_date] += trade.amount
            if trade.side == OrderSide.BUY:
                buy_amount += trade.amount
            else:
                sell_amount += trade.amount

        daily_turnovers: list[float] = []
        for trade_date, equity in equity_curve.items():
            equity_value = float(equity)
            amount = amount_by_date.get(trade_date, 0.0)
            daily_turnovers.append(amount / equity_value if equity_value > 0 else 0.0)

        avg_daily = float(np.mean(daily_turnovers)) if daily_turnovers else 0.0
        return {
            "avg_daily_turnover": avg_daily,
            "annual_turnover": avg_daily * cls.TRADING_DAYS,
            "max_daily_turnover": max(daily_turnovers) if daily_turnovers else 0.0,
            "buy_turnover": buy_amount / avg_assets if avg_assets > 0 else 0.0,
            "sell_turnover": sell_amount / avg_assets if avg_assets > 0 else 0.0,
            "total_turnover": sum(daily_turnovers),
        }

    @staticmethod
    def _exposure_stats(
        account_history: dict[date, AccountSnapshot] | None,
    ) -> dict[str, float]:
        """根据每日 EOD 账户快照计算组合暴露指标。"""
        if not account_history:
            return {
                "avg_position_count": 0.0,
                "max_position_count": 0.0,
                "min_position_count": 0.0,
                "avg_cash_ratio": 0.0,
                "average_exposure": 0.0,
                "max_single_position_weight": 0.0,
                "avg_top5_concentration": 0.0,
            }

        snapshots = [snap for _, snap in sorted(account_history.items(), key=lambda item: item[0])]
        position_counts: list[int] = []
        cash_ratios: list[float] = []
        exposure_ratios: list[float] = []
        top5_concentrations: list[float] = []
        max_single_position_weight = 0.0

        for snapshot in snapshots:
            total_assets = float(snapshot.total_assets)
            position_values = sorted(
                [
                    float(pos.market_value)
                    for pos in snapshot.positions.values()
                    if pos.total_qty > 0 and pos.market_value > 0
                ],
                reverse=True,
            )
            position_counts.append(len(position_values))

            if total_assets > 0:
                cash_ratio = float(snapshot.cash) / total_assets
                weights = [value / total_assets for value in position_values]
                cash_ratios.append(cash_ratio)
                exposure_ratios.append(sum(weights))
                top5_concentrations.append(sum(weights[:5]))
                if weights:
                    max_single_position_weight = max(max_single_position_weight, max(weights))
            else:
                cash_ratios.append(0.0)
                exposure_ratios.append(0.0)
                top5_concentrations.append(0.0)

        return {
            "avg_position_count": float(np.mean(position_counts)) if position_counts else 0.0,
            "max_position_count": float(max(position_counts)) if position_counts else 0.0,
            "min_position_count": float(min(position_counts)) if position_counts else 0.0,
            "avg_cash_ratio": float(np.mean(cash_ratios)) if cash_ratios else 0.0,
            "average_exposure": float(np.mean(exposure_ratios)) if exposure_ratios else 0.0,
            "max_single_position_weight": float(max_single_position_weight),
            "avg_top5_concentration": (
                float(np.mean(top5_concentrations)) if top5_concentrations else 0.0
            ),
        }

    @staticmethod
    def _pair_trades(trades: list[Trade]) -> list[CompletedTrade]:
        """FIFO 方式将 BUY/SELL 配对，支持部分卖出和多笔买入。"""
        buy_queue: dict[str, deque[dict]] = defaultdict(deque)
        completed: list[CompletedTrade] = []

        for trade in sorted(trades, key=lambda t: t.trade_time):
            if trade.side == OrderSide.BUY:
                buy_queue[trade.symbol].append({
                    "quantity": trade.quantity,
                    "cost_per_share": (trade.amount + trade.commission) / trade.quantity,
                    "trade_date": trade.trade_date,
                })
                continue

            remaining = trade.quantity
            if remaining <= 0:
                continue

            proceeds_per_share = (
                (trade.amount - trade.commission - trade.stamp_tax) / trade.quantity
            )
            queue = buy_queue[trade.symbol]
            while remaining > 0 and queue:
                lot = queue[0]
                matched_qty = min(remaining, lot["quantity"])
                total_cost = lot["cost_per_share"] * matched_qty
                net_proceeds = proceeds_per_share * matched_qty
                pnl = net_proceeds - total_cost
                pnl_pct = pnl / total_cost if total_cost > 0 else 0.0
                hold_days = (trade.trade_date - lot["trade_date"]).days

                completed.append(CompletedTrade(
                    symbol=trade.symbol,
                    quantity=matched_qty,
                    buy_date=lot["trade_date"],
                    sell_date=trade.trade_date,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    hold_days=hold_days,
                ))

                lot["quantity"] -= matched_qty
                remaining -= matched_qty
                if lot["quantity"] <= 0:
                    queue.popleft()

        return completed

    @staticmethod
    def _summarize_trades(completed: list[CompletedTrade]) -> dict:
        if not completed:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_profit": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "avg_hold_days": 0.0,
                "realized_pnl": 0.0,
            }

        wins = [t for t in completed if t.pnl_pct > 0]
        losses = [t for t in completed if t.pnl_pct < 0]
        total = len(completed)

        win_rate = len(wins) / total
        avg_profit = float(np.mean([t.pnl_pct for t in wins])) if wins else 0.0
        avg_loss = float(np.mean([t.pnl_pct for t in losses])) if losses else 0.0
        avg_hold_days = float(np.mean([t.hold_days for t in completed]))

        sum_wins = sum(t.pnl for t in wins)
        sum_losses = abs(sum(t.pnl for t in losses))
        profit_factor = (
            sum_wins / sum_losses if sum_losses > 0 else float("inf")
        )

        return {
            "total_trades": total,
            "win_rate": win_rate,
            "avg_profit": avg_profit,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "avg_hold_days": avg_hold_days,
            "realized_pnl": sum(t.pnl for t in completed),
        }

    def compute_benchmark(
        self,
        result: MetricsResult,
        strategy_returns: pd.Series,
        benchmark_returns: pd.Series,
    ) -> MetricsResult:
        """
        计算基准对比指标。

        strategy_returns: 策略日收益率序列（index=trade_date）
        benchmark_returns: 基准日收益率序列（index=trade_date）
        """
        # 对齐日期
        common = strategy_returns.index.intersection(benchmark_returns.index)
        if len(common) < 2:
            return result

        strat = strategy_returns[common]
        bench = benchmark_returns[common]
        excess = strat - bench

        n = len(common)

        # Beta
        cov = float(strat.cov(bench))
        var = float(bench.var())
        beta = round(cov / var, 4) if var > 1e-10 else 0.0

        # 基准和策略在共同日期上的累计收益。
        # bench 是日收益率，不是价格序列，不能用末值/首值相除。
        strat_total = float((1 + strat).prod() - 1)
        bench_total = float((1 + bench).prod() - 1)
        strat_annual = float((1 + strat_total) ** (self.TRADING_DAYS / n) - 1)
        bench_annual = float((1 + bench_total) ** (self.TRADING_DAYS / n) - 1)

        # Alpha (Jensen's)
        alpha = round(
            strat_annual - self.RF_ANNUAL - beta * (bench_annual - self.RF_ANNUAL),
            6,
        )

        # 超额收益
        excess_return = round(strat_total - bench_total, 6)

        # 跟踪误差
        tracking_error = round(float(excess.std() * (self.TRADING_DAYS ** 0.5)), 6)

        # 信息比率
        excess_mean = float(excess.mean())
        information_ratio = round(
            (excess_mean / excess.std() * (self.TRADING_DAYS ** 0.5))
            if excess.std() > 1e-10
            else 0.0,
            4,
        )

        result.excess_return = excess_return
        result.alpha = alpha
        result.beta = beta
        result.information_ratio = information_ratio
        result.tracking_error = tracking_error
        result.benchmark_return = round(bench_total, 6)
        result.benchmark_annual_return = round(bench_annual, 6)
        return result

    @staticmethod
    def _empty_result(initial: float) -> MetricsResult:
        return MetricsResult(
            total_return=0.0, annual_return=0.0, max_drawdown=0.0, volatility=0.0,
            sharpe_ratio=0.0, sortino_ratio=0.0, calmar_ratio=0.0,
            total_trades=0, fill_count=0, round_trip_count=0,
            realized_pnl=0.0, unrealized_pnl=0.0,
            avg_daily_turnover=0.0, annual_turnover=0.0,
            max_daily_turnover=0.0, buy_turnover=0.0, sell_turnover=0.0,
            total_turnover=0.0,
            gross_return=0.0, net_return=0.0,
            gross_annual_return=0.0, net_annual_return=0.0,
            total_slippage_cost=0.0, cost_drag=0.0, cost_to_nav=0.0,
            avg_position_count=0.0, max_position_count=0, min_position_count=0,
            avg_cash_ratio=0.0, average_exposure=0.0,
            max_single_position_weight=0.0, avg_top5_concentration=0.0,
            win_rate=0.0, avg_profit=0.0, avg_loss=0.0,
            profit_factor=0.0, avg_hold_days=0.0,
            total_fees=0.0, final_value=initial, initial_value=initial,
            max_drawdown_start=None, max_drawdown_end=None,
        )
