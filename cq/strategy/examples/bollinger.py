"""
布林带策略（内置示例）。

布林带由三条线组成：
  - 中轨（Middle）= N 日收盘价均线（SMA）
  - 上轨（Upper）  = 中轨 + k 倍标准差
  - 下轨（Lower）  = 中轨 - k 倍标准差

交易规则（均值回归逻辑）：
  - 价格触及或跌破下轨且处于长期趋势上方 → 买入
  - 价格触及或突破上轨 → 卖出（认为超涨，会回归均值）
  - 开启趋势过滤时，跌破长期均线防御性卖出
  - 加仓/减仓条件：当前无/有持仓

适合：低波动、震荡区间明显的股票；趋势行情中可能频繁止损。

参数说明：
  period   = 均值窗口（默认20日）
  std_dev  = 上下轨宽度（默认2倍标准差，覆盖约95%价格分布）
"""

from __future__ import annotations

import pandas as pd

from cq.core.models import Bar
from cq.strategy.base import Strategy
from cq.utils.trading_rules import AStockRules


class BollingerStrategy(Strategy):
    strategy_id = "bollinger"

    def on_init(self) -> None:
        self.period: int = 20        # 布林带窗口（均线和标准差的计算周期）
        self.std_dev: float = 2.0    # 上下轨距离中轨的标准差倍数
        self.position_pct: float = 0.9  # 每次买入占总资产比例
        self.trend_filter_enabled: bool = True
        self.trend_ma: int = 120

    def on_bar(self, bar: Bar) -> None:
        if bar.is_suspended:
            return

        hist_len = max(self.period + 1, self.trend_ma if self.trend_filter_enabled else 0)
        hist = self.ctx.get_bar_history(bar.symbol, n=hist_len)
        if len(hist) < self.period:
            return

        close = hist["close"]
        middle = close.rolling(self.period).mean().iloc[-1]
        std = close.rolling(self.period).std(ddof=1).iloc[-1]   # 样本标准差

        if pd.isna(middle) or pd.isna(std) or std == 0:
            return

        upper = middle + self.std_dev * std
        lower = middle - self.std_dev * std
        current_price = close.iloc[-1]

        has_pos = self.ctx.get_position(bar.symbol) is not None
        trend_ok = True
        if self.trend_filter_enabled:
            if len(close) < self.trend_ma:
                return
            trend_line = close.rolling(self.trend_ma).mean().iloc[-1]
            if pd.isna(trend_line):
                return
            trend_ok = current_price > trend_line
            if has_pos and not trend_ok:
                self.sell(bar.symbol)
                return

        # 价格触及下轨：超卖，买入
        if current_price <= lower:
            if trend_ok and not has_pos and not AStockRules.is_limit_up(bar):
                self.buy(bar.symbol, percent=self.position_pct)

        # 价格触及上轨：超买，卖出
        elif current_price >= upper:
            if has_pos:
                self.sell(bar.symbol)
