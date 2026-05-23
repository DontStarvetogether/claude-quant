# 架构总览

## 一、核心设计目标

| 目标 | 具体要求 |
|------|---------|
| 回测正确性 | 无前视偏差、正确 T+1、涨跌停处理、除权连续 |
| 策略可移植性 | 回测与实盘策略代码完全一致，零修改 |
| 模块独立性 | 每层可独立测试，依赖单向流动（下层不知道上层） |
| 可扩展性 | 新增数据源、新增执行接口、新增风控规则均不破坏现有代码 |

---

## 二、层次结构与依赖方向

```
┌─────────────────────────────────────────────────────┐
│  cq CLI / scripts / Web  （用户入口）                 │
├─────────────────────────────────────────────────────┤
│  strategy/               （策略层）                   │
│    只通过 StrategyContext 只读访问状态                  │
│    只通过 SignalEvent 发出交易意图                      │
├─────────────────────────────────────────────────────┤
│  engine/                 （引擎层）                    │
│  ├── BacktestEngine / LiveEngine  主循环              │
│  ├── PortfolioManager             持仓+资金管理        │
│  └── matching/                    撮合（回测/实盘）     │
├─────────────────────────────────────────────────────┤
│  execution/  risk/        （执行+风控层）              │
│    execution：SignalEvent → OrderEvent               │
│    risk：下单前校验，可拒绝信号                         │
├─────────────────────────────────────────────────────┤
│  core/                   （核心层）                    │
│  ├── models.py            所有数据类（Bar/Order/...）   │
│  ├── events.py            所有事件类型定义              │
│  └── event_bus.py         优先队列事件总线              │
├─────────────────────────────────────────────────────┤
│  data/                   （数据层）                    │
│  ├── source/              外部数据源（akshare/baostock）│
│  ├── store/               本地 Parquet 存储             │
│  ├── feed/                向引擎推送 BarEvent           │
│  └── adjust/              复权计算                     │
└─────────────────────────────────────────────────────┘

依赖方向：上层依赖下层，下层不知道上层。
唯一例外：EventBus 是全局消息通道，各层均可发布/订阅。
```

---

## 三、事件总线设计

### 为什么用优先队列

同一个交易日内，事件必须按照正确顺序处理：
1. 先拿到行情（`BarEvent`）
2. 策略才能产生信号（`SignalEvent`）
3. 信号经风控才能变成订单（`OrderEvent`）
4. 撮合引擎处理订单后产生成交（`FillEvent`）
5. 最后结算持仓（`EndOfDayEvent`）

用堆（heapq）实现，同优先级保证 FIFO：

```
EventPriority:
  MARKET_DATA = 10   BarEvent, TickEvent
  SIGNAL      = 20   SignalEvent
  ORDER       = 30   OrderEvent
  FILL        = 40   FillEvent, RejectEvent
  EOD         = 90   EndOfDayEvent
```

### 同步事件模型

- **回测**：同步 `EventBus`，单线程，确保可重现。
- **模拟盘 / 实盘**：`LiveEngine` 仍复用同步 `EventBus`，外部行情/券商回报通过线程安全队列进入主循环。
- 当前没有单独的 `AsyncEventBus` 实现；实盘的网络 IO 隔离在 feed / executor 层。

---

## 四、回测正确性保证

### 4.1 无前视偏差（Look-ahead Bias）

**问题**：现有系统在 D 日 bar 上产生信号，立即用 D 日收盘价成交 —— 实际上不可能。

**解决方案**：

```
D 日：
  策略 on_bar(D) → 产生 SignalEvent
  SignalEvent → 通过风控 → 生成 Order，放入 pending_orders[D]
  D 日不撮合

D+1 日 before_trading：
  取出 pending_orders[D]
  用 D+1 的 open 价格撮合
  产生 FillEvent
```

`BarMatchingEngine` 持有 `pending_orders: dict[date, list[Order]]`，确保时序正确。

### 4.2 T+1 交割

```
买入发生：
  Position.total_qty += qty
  Position.today_bought_qty += qty
  Position.tradeable_qty 不变（不增加）

每日 EOD settle：
  Position.tradeable_qty += Position.today_bought_qty
  Position.today_bought_qty = 0
```

卖出时检查 `tradeable_qty`，不足则拒绝并发出 `RejectEvent`。

### 4.3 涨跌停处理

```
买入时（D+1 开盘撮合）：
  if D+1.open >= D+1.limit_up:
    拒绝（涨停开盘无法买入）
  else:
    以 D+1.open 成交

卖出时（D+1 开盘撮合）：
  if D+1.open <= D+1.limit_down and D+1.close <= D+1.limit_down:
    拒绝（跌停封板无法卖出）
  else:
    以 D+1.open 成交

涨跌停价计算：
  普通股：pre_close * (1 ± 0.10)，精确到分
  ST股：  pre_close * (1 ± 0.05)
  北交所/创业板注册制：pre_close * (1 ± 0.20)
```

### 4.4 除权连续性

K 线数据存储两套：
- `raw.parquet`：原始未复权价格（永远追加，不覆写）
- `qfq.parquet`：前复权价格（触发除权时重算整个序列）

策略使用 `qfq` 数据，保证价格序列连续。
涨跌停判断使用 `raw` 数据的 `pre_close` 字段（前一日收盘原始价）。

---

## 五、策略层隔离设计

策略不直接操作账户，通过两个接口与外界交互：

**读**：`StrategyContext`（只读视图）

```python
ctx.get_position("000001.SZ")  # 返回 Position 快照（frozen）
ctx.get_cash()                  # 当前可用资金
ctx.get_bar_history("000001.SZ", n=60)  # 最近 60 根 bar 的 DataFrame
ctx.get_trade_date()            # 当前处理日期
```

**写**：`self.buy()` / `self.sell()` → 发出 `SignalEvent`

引擎根据当前模式将 `SignalEvent` 路由到：
- 回测：`SimulatedExecutor` → `BarMatchingEngine`
- 实盘：`QMTExecutor` → 券商 API

策略代码对此完全无感。

---

## 六、数据层设计

### 目录结构（ParquetStore）

```
{data_root}/
  bars/
    SH/
      600519/
        raw.parquet      原始未复权 OHLCV + 涨跌停价
        qfq.parquet      前复权 OHLCV
        adj_factors.parquet  复权因子序列
    SZ/
      000001/
        ...
  calendar/
    SSE.parquet          上交所交易日历
    SZSE.parquet         深交所交易日历
  stock_info/
    all.parquet          全市场股票基础信息
```

### 数据下载幂等性

`write_daily_bars(mode="append")` 保证：
1. 读取现有数据
2. 合并新数据
3. 按 `trade_date` 去重（保留新数据）
4. 写回

中断重试不会产生重复数据。

### Schema 版本控制

每个 Parquet 文件写入 metadata：

```python
{
    "cq_schema_version": "1",
    "source": "baostock",
    "adjust_type": "qfq",
    "created_at": "2024-01-01T00:00:00",
}
```

读取时校验版本，不兼容强制重新下载。

---

## 七、关键接口定义（ABC 一览）

| ABC | 实现类 | 职责 |
|-----|--------|------|
| `DataSource` | `AkshareSource`, `BaostockSource` | 从外部 API 拉取标准化数据 |
| `DataStore` | `ParquetStore` | 本地数据读写（Parquet） |
| `DataFeed` | `HistoricalFeed`, `RealtimeFeed` | 向引擎推送 `BarEvent` |
| `MatchingEngine` | `BarMatchingEngine` | D+1 订单撮合 |
| `Strategy` | 用户自定义 + 内置示例 | 策略逻辑 |
| `Executor` | `SimulatedExecutor`, `QMTExecutor` | 信号 → 订单路由 |
| `RiskGuard` | `PreTradeRisk`, `PortfolioRisk` | 风控检查 |

---

## 八、配置管理

```yaml
# config/default.yaml
engine:
  initial_capital: 1_000_000
  commission_rate: 0.0003    # 万三
  stamp_tax_rate: 0.001      # 千一（卖出）
  min_commission: 5.0        # 最低5元
  slippage: 0.0              # 回测默认无滑点

data:
  root: "~/.cq/data"
  source: "akshare"          # akshare | baostock

risk:
  max_position_pct: 0.20     # 单股最大仓位 20%
  max_drawdown_stop: 0.15    # 最大回撤止损 15%
  min_cash_reserve: 0.05     # 最低现金储备 5%
```

**Token 安全**：永远从环境变量读取，配置文件不存储任何密钥。

---

## 九、测试策略概览

```
tests/
  unit/           每个类的独立测试，纯内存，< 1s/个
  integration/    完整流程测试，使用 fixture 数据，< 10s/个
  property/       hypothesis 属性测试，验证数学不变式
  fixtures/       固定测试数据（parquet + json）
```

关键不变式（property tests）：

1. **资金守恒**：任意交易序列后，`total_assets = initial + realized_pnl - fees`
2. **T+1 约束**：任意策略，当日买入的 `tradeable_qty` 在 EOD 前为 0
3. **复权连续**：除权前后组合总市值不发生突变（> 0.01%）
4. **优先级顺序**：同日内 MARKET_DATA 永远先于 SIGNAL 先于 ORDER 先于 FILL 处理
