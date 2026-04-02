"""
RSI 策略（内置示例）。

RSI（相对强弱指数）衡量近期涨跌力度：
  - RSI < 超卖阈值（默认30）→ 买入（市场可能超卖，等待反弹）
  - RSI > 超买阈值（默认70）→ 卖出（市场可能过热，等待回调）

适合：震荡市中单只股票，趋势明显时容易逆势亏损。

RSI 计算方式（Wilder 平滑法）：
  1. 计算每日涨跌幅：gain = max(close - prev, 0)，loss = max(prev - close, 0)
  2. 对 gain 和 loss 分别做 EMA（com = period - 1）
  3. RS = avg_gain / avg_loss；RSI = 100 - 100 / (1 + RS)
"""

from __future__ import annotations

import pandas as pd

from cq.core.models import Bar
from cq.strategy.base import Strategy
from cq.utils.trading_rules import AStockRules


class RsiStrategy(Strategy):
    strategy_id = "rsi"

    def on_init(self) -> None:
        self.period: int = 14       # RSI 计算周期
        self.oversold: float = 30   # 超卖阈值（低于此值买入）
        self.overbought: float = 70 # 超买阈值（高于此值卖出）
        self.position_pct: float = 0.9  # 每次买入占总资产比例

    def on_bar(self, bar: Bar) -> None:
        if bar.is_suspended:
            return

        # 需要 period + 1 根 bar 才能计算稳定的 RSI
        hist = self.ctx.get_bar_history(bar.symbol, n=self.period + 2)
        if len(hist) < self.period + 1:
            return

        rsi = _calc_rsi(hist["close"], self.period)
        if rsi is None:
            return

        has_pos = self.ctx.get_position(bar.symbol) is not None

        # 超卖买入
        if rsi < self.oversold:
            if not has_pos and not AStockRules.is_limit_up(bar):
                self.buy(bar.symbol, percent=self.position_pct)

        # 超买卖出
        elif rsi > self.overbought:
            if has_pos:
                self.sell(bar.symbol)


def _calc_rsi(close: pd.Series, period: int) -> float | None:
    """
    用 Wilder 平滑法（EMA）计算 RSI，返回最新一根的值。

    为什么用 EMA 而不是简单平均：
      Wilder 原版用递归平滑（等价于 com=period-1 的 EWM），
      比 SMA 更平滑，对近期变化反应更灵敏。
    """
    delta = close.diff()
    gain = delta.clip(lower=0)   # 涨幅，跌时为 0
    loss = (-delta).clip(lower=0)  # 跌幅，涨时为 0

    # EWM 指数加权平均（com = period - 1 等价于 alpha = 1/period）
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]

    if pd.isna(last_gain) or pd.isna(last_loss):
        return None
    if last_loss == 0:
        return 100.0  # 没有下跌 → RSI 极值 100

    rs = last_gain / last_loss
    return 100.0 - (100.0 / (1.0 + rs))
