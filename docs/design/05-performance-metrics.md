# 绩效指标

## 设计原则

1. **全量计算，不在线计算**：所有指标在回测结束后一次性计算，主循环只记录快照。
2. **基于交易日**：年化计算使用 252 个交易日，不用自然日。
3. **交易对配对**：用 FIFO 方式将买卖配对，计算每笔交易盈亏。
4. **结果可复现**：给定相同的 equity_curve 和 trades，结果完全确定。

---

## 指标公式

### 1. 总收益率

```
total_return = (final_value - initial_value) / initial_value
```

### 2. 年化收益率（CAGR）

```
n_days = 回测期间交易日数（不是自然日）
annual_return = (1 + total_return) ^ (252 / n_days) - 1
```

**注意**：回测天数 < 252 时，年化会放大收益，需结合绝对值解读。

### 3. 最大回撤

```
rolling_max(t) = max(equity_curve[0..t])
drawdown(t) = (equity_curve(t) - rolling_max(t)) / rolling_max(t)
max_drawdown = min(drawdown)    # 负数，如 -0.15 表示 15% 最大回撤
```

最大回撤发生区间（峰值日 → 谷值日）：

```python
peak_idx = (equity_curve.cummax() == equity_curve).idxmax()
trough_idx = drawdown.idxmin()
recovery_idx = ...  # trough 之后首次回到 rolling_max 的日期
```

### 4. 夏普比率

```
rf_daily = 0.03 / 252           # 无风险利率 3%/年
daily_returns = equity_curve.pct_change()
excess = daily_returns - rf_daily
sharpe = mean(excess) / std(excess) * sqrt(252)
```

**注意**：`std` 使用样本标准差（ddof=1），与行业惯例一致。

### 5. 卡玛比率（Calmar Ratio）

```
calmar = annual_return / abs(max_drawdown)
```

卡玛比率 > 1 通常视为较好（每承担 1% 回撤，获得 > 1% 年化收益）。

### 6. 索提诺比率

```
downside_returns = daily_returns[daily_returns < rf_daily] - rf_daily
downside_std = sqrt(mean(downside_returns^2))    # 下行标准差
sortino = mean(excess) / downside_std * sqrt(252)
```

### 7. 波动率（年化）

```
volatility = std(daily_returns) * sqrt(252)
```

---

## 交易统计

### 配对逻辑（FIFO）

```python
def _calc_trade_stats(trades: list[Trade]) -> TradeStats:
    """
    用 FIFO 将 BUY/SELL 配对，计算每笔完整交易的盈亏。
    """
    buy_queue: dict[str, deque[Trade]] = defaultdict(deque)
    completed_trades: list[CompletedTrade] = []

    for trade in sorted(trades, key=lambda t: t.trade_time):
        if trade.side == OrderSide.BUY:
            buy_queue[trade.symbol].append(trade)
        elif buy_queue[trade.symbol]:
            buy_trade = buy_queue[trade.symbol].popleft()
            # 总成本（含买卖双向手续费和印花税）
            total_cost = buy_trade.amount + buy_trade.commission
            total_proceeds = trade.amount - trade.commission - trade.stamp_tax
            pnl = total_proceeds - total_cost
            pnl_pct = pnl / total_cost
            completed_trades.append(CompletedTrade(
                symbol=trade.symbol,
                buy_date=buy_trade.trade_date,
                sell_date=trade.trade_date,
                pnl=pnl,
                pnl_pct=pnl_pct,
                hold_days=(trade.trade_date - buy_trade.trade_date).days,
            ))

    return _summarize(completed_trades)
```

### 汇总指标

| 指标 | 说明 |
|------|------|
| `win_rate` | 盈利交易数 / 总交易数 |
| `avg_profit` | 盈利交易的平均收益率 |
| `avg_loss` | 亏损交易的平均亏损率（负数） |
| `profit_factor` | `sum(盈利)` / `abs(sum(亏损))` |
| `avg_hold_days` | 平均持仓天数（自然日） |
| `max_consecutive_wins` | 最大连续盈利次数 |
| `max_consecutive_losses` | 最大连续亏损次数 |

---

## PerformanceMetrics 完整实现

```python
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
    # 最大回撤细节
    max_drawdown_start: date
    max_drawdown_end: date


class PerformanceMetrics:

    RF_ANNUAL = 0.03           # 无风险利率
    TRADING_DAYS = 252

    def compute(
        self,
        equity_curve: pd.Series,   # index=date, values=净资产
        trades: list[Trade],
        initial_capital: float,
    ) -> MetricsResult:

        rf_daily = self.RF_ANNUAL / self.TRADING_DAYS
        daily_returns = equity_curve.pct_change().dropna()

        n_days = len(equity_curve) - 1
        initial = equity_curve.iloc[0]
        final = equity_curve.iloc[-1]

        total_return = (final - initial) / initial
        annual_return = (
            (1 + total_return) ** (self.TRADING_DAYS / n_days) - 1
            if n_days > 0 else 0.0
        )

        # 最大回撤
        rolling_max = equity_curve.cummax()
        drawdown = (equity_curve - rolling_max) / rolling_max
        max_drawdown = drawdown.min()
        dd_end = drawdown.idxmin()
        dd_start = equity_curve[:dd_end].idxmax()

        # 夏普
        excess = daily_returns - rf_daily
        sharpe = (
            excess.mean() / excess.std() * np.sqrt(self.TRADING_DAYS)
            if excess.std() > 1e-10 else 0.0
        )

        # 索提诺
        downside = excess[excess < 0]
        downside_std = np.sqrt((downside ** 2).mean()) if len(downside) > 0 else 1e-10
        sortino = excess.mean() / downside_std * np.sqrt(self.TRADING_DAYS)

        # 卡玛
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        # 交易统计
        trade_stats = self._calc_trade_stats(trades)

        total_fees = sum(t.commission + t.stamp_tax for t in trades)

        return MetricsResult(
            total_return=round(total_return, 6),
            annual_return=round(annual_return, 6),
            max_drawdown=round(max_drawdown, 6),
            volatility=round(daily_returns.std() * np.sqrt(self.TRADING_DAYS), 6),
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            calmar_ratio=round(calmar, 4),
            total_trades=trade_stats.total_trades,
            win_rate=round(trade_stats.win_rate, 4),
            avg_profit=round(trade_stats.avg_profit, 6),
            avg_loss=round(trade_stats.avg_loss, 6),
            profit_factor=round(trade_stats.profit_factor, 4),
            avg_hold_days=round(trade_stats.avg_hold_days, 1),
            total_fees=round(total_fees, 2),
            final_value=round(final, 2),
            max_drawdown_start=dd_start,
            max_drawdown_end=dd_end,
        )
```

---

## BacktestResult.summary() 输出格式

```
策略：double_ma
标的：600519.SH, 000001.SZ
区间：2022-01-01 → 2024-12-31（共 727 个交易日）
初始资金：1,000,000 元

═══════════════════════════════════════
收益概况
  总收益率          +23.45%
  年化收益率        +8.91%
  最终净值          1,234,500 元

风险指标
  最大回撤          -12.30%  (2022-03-10 → 2022-04-27)
  年化波动率        15.23%
  夏普比率          0.5821
  索提诺比率        0.8134
  卡玛比率          0.7244

交易统计
  总交易次数        48 笔
  胜率              58.33%
  平均盈利          +4.52%
  平均亏损          -2.87%
  盈亏比            1.84
  平均持仓          18.3 天

费用
  总手续费+印花税   3,420 元
═══════════════════════════════════════
```

---

## 基准对比（可选）

回测结果可选传入基准（如沪深300指数）进行对比：

```python
result.compare_benchmark(
    benchmark_prices=sh300_series,  # pd.Series, index=date, values=price
)
```

额外计算：
- **超额收益**：策略年化 - 基准年化
- **信息比率**：超额收益 / 超额收益标准差 × √252
- **Beta**：策略日收益与基准日收益的回归系数
- **Alpha**：策略年化 - (rf + beta × (基准年化 - rf))
