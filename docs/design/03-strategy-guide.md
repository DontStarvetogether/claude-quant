# 策略开发指南

## 最小示例

```python
from cq.strategy.base import Strategy
from cq.core.models import Bar
import pandas_ta as ta

class MyStrategy(Strategy):
    strategy_id = "my_strategy"

    def on_init(self) -> None:
        self.fast = 5
        self.slow = 20

    def on_bar(self, bar: Bar) -> None:
        # 获取历史数据
        hist = self.ctx.get_bar_history(bar.symbol, n=self.slow + 1)
        if len(hist) < self.slow + 1:
            return

        ma_fast = hist["close"].rolling(self.fast).mean().iloc[-1]
        ma_slow = hist["close"].rolling(self.slow).mean().iloc[-1]
        ma_fast_prev = hist["close"].rolling(self.fast).mean().iloc[-2]
        ma_slow_prev = hist["close"].rolling(self.slow).mean().iloc[-2]

        has_pos = self.ctx.get_position(bar.symbol) is not None

        # 金叉买入
        if ma_fast > ma_slow and ma_fast_prev <= ma_slow_prev:
            if not has_pos:
                self.buy(bar.symbol, percent=0.95)  # 用 95% 资金买入

        # 死叉卖出
        elif ma_fast < ma_slow and ma_fast_prev >= ma_slow_prev:
            if has_pos:
                self.sell(bar.symbol)  # 全部卖出
```

运行回测：

```python
from cq.engine.backtest_engine import BacktestEngine
from cq.utils.config import Config

config = Config.from_yaml("config/local.yaml")
engine = BacktestEngine(config)
engine.add_strategy(MyStrategy(), symbols=["600519.SH", "000001.SZ"])
result = engine.run("2022-01-01", "2024-12-31")
print(result.summary())
```

---

## Strategy ABC 完整接口

### 生命周期

```
on_init()                   引擎 add_strategy 时调用，做初始化
  │
  ▼（对每个交易日）
before_trading(trade_date)  当日 bar 推送前调用
  │
  ▼（对当日每根 Bar）
on_bar(bar)                 每根 Bar 触发
  │
  ├─ [可选] 产生 SignalEvent（通过 self.buy/sell）
  ▼
on_order_update(event)      收到成交或拒绝回报
  │
  ▼
after_trading(trade_date)   当日所有 bar 处理完，EOD settle 后调用
```

### StrategyContext 可用接口

```python
# 持仓查询
pos = self.ctx.get_position("600519.SH")  # None 或 Position 快照
if pos:
    print(pos.total_qty)         # 总持仓
    print(pos.tradeable_qty)     # 可卖数量（T+1 约束）
    print(pos.avg_cost)          # 持仓均价
    print(pos.unrealized_pnl)    # 浮动盈亏

# 资金查询
cash = self.ctx.get_cash()           # 可用现金
total = self.ctx.get_total_assets()  # 总资产（现金+持仓市值）

# 历史数据（供技术指标计算）
hist = self.ctx.get_bar_history("600519.SH", n=60)
# 返回 DataFrame，列：trade_date, open, high, low, close, volume, amount
# 已按 trade_date 升序排列，最新 bar 在最后一行

# 日期
today = self.ctx.get_trade_date()    # 当前处理的交易日期（date 类型）
```

### 下单接口

```python
# 买入
self.buy(
    symbol="600519.SH",
    price=None,            # None=次日开盘市价，float=限价
    quantity=None,         # 指定股数（100的整数倍）
    percent=0.1,           # 占总资产 10%（与 quantity/amount 三选一）
    amount=50000.0,        # 买入金额（与 quantity/percent 三选一）
)

# 卖出
self.sell(
    symbol="600519.SH",
    price=None,
    quantity=None,         # None=全部卖出
    percent=1.0,           # 卖出持仓的 100%（与 quantity 二选一）
)

# 撤单
self.cancel(order_id="B20240101001")
```

**注意事项**：

1. `percent` 参数基于**总资产**计算，不是可用现金。`amount` 基于绝对金额。
2. 买入数量自动向下取整到 100 股。如果计算结果 < 100 股，**不下单**（而非旧系统的以 0 股下单）。
3. 下单后**不立即成交**，次日开盘才会尝试撮合。
4. 如果资金不足、涨跌停封板、T+1 限制，会收到 `RejectEvent`，`on_order_update` 被调用。

---

## 多股票策略

`add_strategy()` 可以传入多个 symbol，`on_bar` 会对每只股票的每根 Bar 分别调用：

```python
class MultiStockStrategy(Strategy):
    strategy_id = "multi_stock"

    def on_bar(self, bar: Bar) -> None:
        # bar.symbol 告诉你当前是哪只股票的 bar
        hist = self.ctx.get_bar_history(bar.symbol, n=20)
        ...
```

如果需要跨股票决策（如选最强的），使用 `after_trading` 收集当日所有 bar 后再决策：

```python
class RankStrategy(Strategy):
    strategy_id = "rank_strategy"

    def on_init(self):
        self._today_bars: dict[str, Bar] = {}

    def on_bar(self, bar: Bar) -> None:
        self._today_bars[bar.symbol] = bar

    def after_trading(self, trade_date: str) -> None:
        # 所有 bar 已收集，可以跨股票比较
        ranked = sorted(
            self._today_bars.values(),
            key=lambda b: (b.close - b.open) / b.open,
            reverse=True
        )
        self._today_bars.clear()

        # 买入当日涨幅最大的前5只
        for bar in ranked[:5]:
            if self.ctx.get_position(bar.symbol) is None:
                self.buy(bar.symbol, percent=0.18)
```

---

## 最佳实践

### 1. 不在 on_bar 中做全量历史计算

```python
# ❌ 每次 on_bar 都重新计算整个序列（慢）
def on_bar(self, bar: Bar):
    hist = self.ctx.get_bar_history(bar.symbol, n=500)
    ma = hist["close"].rolling(20).mean()   # 每次算500个点

# ✅ 只请求必要长度，引擎已预加载
def on_bar(self, bar: Bar):
    hist = self.ctx.get_bar_history(bar.symbol, n=21)  # 只需要21个点
    ma = hist["close"].rolling(20).mean().iloc[-1]     # 只取最新值
```

### 2. 使用 pandas-ta 而非手写指标

```python
import pandas_ta as ta

def on_bar(self, bar: Bar):
    hist = self.ctx.get_bar_history(bar.symbol, n=35)
    # MACD：12/26/9
    macd = ta.macd(hist["close"], fast=12, slow=26, signal=9)
    hist_col = macd["MACDh_12_26_9"]
    if hist_col.iloc[-1] > 0 and hist_col.iloc[-2] <= 0:
        self.buy(bar.symbol, percent=0.2)
```

### 3. 不依赖 on_order_update 做仓位判断

```python
# ❌ 依赖 on_order_update 更新内部状态（可能丢失）
def on_order_update(self, event):
    if isinstance(event, FillEvent):
        self._has_position[event.trade.symbol] = True

# ✅ 直接查 context（始终准确）
def on_bar(self, bar: Bar):
    has_pos = self.ctx.get_position(bar.symbol) is not None
```

### 4. 涨跌停判断

```python
from cq.utils.trading_rules import AStockRules

def on_bar(self, bar: Bar):
    # 不要买涨停股（次日可能无法买入）
    if AStockRules.is_limit_up(bar):
        return
    # 不要卖跌停股（次日可能无法卖出）
    if AStockRules.is_limit_down(bar) and self.ctx.get_position(bar.symbol):
        return   # 跌停了也卖不出去，不发信号（避免产生 RejectEvent）
    ...
```
