"""
双均线策略（内置示例）。

金叉（快线上穿慢线）买入，死叉（快线下穿慢线）卖出。
"""

from __future__ import annotations

from cq.core.models import Bar
from cq.strategy.base import Strategy
from cq.utils.trading_rules import AStockRules


class DoubleMaStrategy(Strategy):
    strategy_id = "double_ma"

    def on_init(self) -> None:
        self.fast: int = 5
        self.slow: int = 20

    def on_bar(self, bar: Bar) -> None:
        # 停牌跳过
        if bar.is_suspended:
            return

        hist = self.ctx.get_bar_history(bar.symbol, n=self.slow + 1)
        if len(hist) < self.slow + 1:
            return

        close = hist["close"]
        ma_fast = close.rolling(self.fast).mean()
        ma_slow = close.rolling(self.slow).mean()

        curr_fast = ma_fast.iloc[-1]
        curr_slow = ma_slow.iloc[-1]
        prev_fast = ma_fast.iloc[-2]
        prev_slow = ma_slow.iloc[-2]

        has_pos = self.ctx.get_position(bar.symbol) is not None

        # 金叉买入（快线上穿慢线）
        if curr_fast > curr_slow and prev_fast <= prev_slow:
            if not has_pos and not AStockRules.is_limit_up(bar):
                self.buy(bar.symbol, percent=0.9)

        # 死叉卖出（快线下穿慢线）
        elif curr_fast < curr_slow and prev_fast >= prev_slow and has_pos:
            self.sell(bar.symbol)
