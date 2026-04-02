"""
动量策略（内置示例）。

动量（Momentum）= 股票在过去 N 日内的涨跌幅。

交易规则（趋势跟随逻辑）：
  - N 日涨幅 > 买入阈值  且 无持仓 → 买入（强者恒强，跟随上涨趋势）
  - N 日涨幅 < 卖出阈值  且 有持仓 → 卖出（弱者恒弱，离场止损）

与双均线策略的区别：
  - 双均线看「两条线的交叉」，有滞后
  - 动量策略直接看「价格的涨幅」，更直接，但对噪音更敏感

适合：市场趋势分化明显时；震荡市中频繁误判。

参数说明：
  lookback        = 计算涨幅的回看天数（默认20日）
  buy_threshold   = 买入涨幅阈值（默认5%，即20日涨幅 > 5% 才买入）
  sell_threshold  = 卖出涨幅阈值（默认-5%，即20日跌幅 > 5% 时离场）
"""

from __future__ import annotations

import pandas as pd

from cq.core.models import Bar
from cq.strategy.base import Strategy
from cq.utils.trading_rules import AStockRules


class MomentumStrategy(Strategy):
    strategy_id = "momentum"

    def on_init(self) -> None:
        self.lookback: int = 20          # 回看天数（计算区间涨跌幅）
        self.buy_threshold: float = 0.05  # 买入阈值（区间涨幅 > 5%）
        self.sell_threshold: float = -0.05  # 卖出阈值（区间涨幅 < -5%）
        self.position_pct: float = 0.9    # 每次买入占总资产比例

    def on_bar(self, bar: Bar) -> None:
        if bar.is_suspended:
            return

        # 需要 lookback + 1 根 bar（用于计算起点价格）
        hist = self.ctx.get_bar_history(bar.symbol, n=self.lookback + 1)
        if len(hist) < self.lookback + 1:
            return

        close = hist["close"]
        price_now = close.iloc[-1]
        price_then = close.iloc[-(self.lookback + 1)]  # lookback 天前的收盘价

        if price_then <= 0:
            return

        # N 日涨幅
        momentum = (price_now - price_then) / price_then

        has_pos = self.ctx.get_position(bar.symbol) is not None

        # 强势买入
        if momentum > self.buy_threshold:
            if not has_pos and not AStockRules.is_limit_up(bar):
                self.buy(bar.symbol, percent=self.position_pct)

        # 弱势离场
        elif momentum < self.sell_threshold:
            if has_pos:
                self.sell(bar.symbol)
