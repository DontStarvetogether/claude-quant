"""
趋势强度排名选股策略。

核心思路：
  - 每日对股票池做流动性、趋势结构、动量过滤
  - 按近 N 日涨幅 / 近期波动率排序，买入风险调整后排名靠前的股票
  - 最多持有 max_holdings 只，跌破退出均线/移动止损/跌出退出排名时卖出

这是一个组合级策略，决策发生在 after_trading()：
当天收盘后完成排名并提交信号，次日开盘撮合，仍然没有前视偏差。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from cq.core.models import Bar
from cq.strategy.base import Strategy
from cq.utils.trading_rules import AStockRules


@dataclass
class _Candidate:
    symbol: str
    score: float
    momentum: float
    avg_amount: float


class TrendRankStrategy(Strategy):
    strategy_id = "trend_rank"

    def on_init(self) -> None:
        self.momentum_lookback: int = 60
        self.fast_ma: int = 20
        self.slow_ma: int = 60
        self.liquidity_lookback: int = 20
        self.min_avg_amount: float = 50_000_000
        self.min_momentum: float = 0.03
        self.top_n: int = 30
        self.rank_exit_n: int = 60
        self.max_holdings: int = 5
        self.position_pct: float = 0.2
        self.exit_ma: int = 20
        self.trailing_stop: float = 0.08
        self.rank_exit: bool = True
        self.volatility_lookback: int = 20

        self._seen_symbols: set[str] = set()
        self._today_candidates: list[_Candidate] = []
        self._today_exits: set[str] = set()
        self._peak_close: dict[str, float] = {}

    def before_trading(self, trade_date: date) -> None:
        self._today_candidates = []
        self._today_exits = set()

    def on_bar(self, bar: Bar) -> None:
        self._seen_symbols.add(bar.symbol)

        pos = self.ctx.get_position(bar.symbol)
        has_pos = pos is not None

        if bar.is_suspended:
            return
        if bar.is_st:
            if has_pos:
                self._today_exits.add(bar.symbol)
            return

        hist_len = max(
            self.momentum_lookback + 1,
            self.slow_ma,
            self.exit_ma,
            self.liquidity_lookback,
            self.volatility_lookback + 1,
        )
        hist = self.ctx.get_bar_history(bar.symbol, n=hist_len)
        if len(hist) < hist_len:
            return

        close = hist["close"]
        amount = hist["amount"]
        current_close = float(close.iloc[-1])
        if current_close <= 0:
            return

        if has_pos:
            self._peak_close[bar.symbol] = max(
                self._peak_close.get(bar.symbol, current_close),
                current_close,
            )
        else:
            self._peak_close.pop(bar.symbol, None)

        exit_ma_val = close.rolling(self.exit_ma).mean().iloc[-1]
        if has_pos:
            peak = self._peak_close.get(bar.symbol, current_close)
            drawdown_from_peak = (current_close - peak) / peak if peak > 0 else 0.0
            if current_close < exit_ma_val or drawdown_from_peak <= -self.trailing_stop:
                self._today_exits.add(bar.symbol)

        fast = close.rolling(self.fast_ma).mean().iloc[-1]
        slow = close.rolling(self.slow_ma).mean().iloc[-1]
        avg_amount = float(amount.tail(self.liquidity_lookback).mean())
        price_then = float(close.iloc[-(self.momentum_lookback + 1)])
        momentum = (current_close - price_then) / price_then if price_then > 0 else 0.0
        volatility = close.pct_change().tail(self.volatility_lookback).std(ddof=1)

        if (
            pd.isna(fast)
            or pd.isna(slow)
            or pd.isna(volatility)
            or volatility <= 0
            or avg_amount < self.min_avg_amount
            or momentum < self.min_momentum
            or not (current_close > fast > slow)
        ):
            if has_pos:
                self._today_exits.add(bar.symbol)
            return

        if not AStockRules.is_limit_up(bar):
            self._today_candidates.append(
                _Candidate(
                    symbol=bar.symbol,
                    score=momentum / float(volatility),
                    momentum=momentum,
                    avg_amount=avg_amount,
                )
            )

    def after_trading(self, trade_date: date) -> None:
        ranked = sorted(self._today_candidates, key=lambda c: c.score, reverse=True)
        exit_rank_n = max(self.rank_exit_n, self.top_n)
        exit_symbols = {c.symbol for c in ranked[:exit_rank_n]}

        held_symbols = {
            symbol
            for symbol in self._seen_symbols
            if self.ctx.get_position(symbol) is not None
        }

        if self.rank_exit:
            for symbol in held_symbols:
                if symbol not in exit_symbols:
                    self._today_exits.add(symbol)

        for symbol in sorted(self._today_exits):
            if self.ctx.get_position(symbol) is not None:
                self.sell(symbol)
                self._peak_close.pop(symbol, None)

        exiting = {
            symbol
            for symbol in self._today_exits
            if self.ctx.get_position(symbol) is not None
        }
        effective_holdings = len(held_symbols - exiting)
        slots = max(0, self.max_holdings - effective_holdings)
        if slots <= 0:
            return

        for candidate in ranked:
            if slots <= 0:
                break
            if candidate.symbol in held_symbols or candidate.symbol in self._today_exits:
                continue
            self.buy(candidate.symbol, percent=self.position_pct)
            slots -= 1
