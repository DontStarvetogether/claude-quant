# claude-quant

A股量化交易框架，以**生产实盘**为最终目标设计。

## 设计原则

1. **事件驱动**：回测与实盘共用同一套事件流，策略代码零改动切换
2. **正确优先**：T+1 交割、涨跌停封板、除权除息，每个细节都正确建模
3. **模块独立**：数据层、引擎层、策略层、执行层各自可独立测试
4. **显式胜于隐式**：Pydantic 模型校验、强类型、无全局状态

## 项目结构

```
cq/                     核心包
├── core/               数据模型 + 事件总线
├── data/               数据层（数据源、本地存储、复权、日历）
├── engine/             回测引擎 + 撮合引擎 + 投资组合管理
├── strategy/           策略基类 + 内置示例策略
├── execution/          执行层（模拟 / QMT 实盘）
├── risk/               风控层（下单前检查、组合风控）
├── performance/        绩效指标 + 报告生成
└── utils/              日志、配置、A股规则工具

tests/                  测试套件
├── unit/               单元测试（纯内存，无外部依赖）
├── integration/        集成测试（完整流程验证）
└── property/           属性测试（hypothesis，验证数学不变式）

scripts/                CLI 工具
├── download_data.py    下载并存储历史 K 线
├── run_backtest.py     运行回测并输出报告
└── sync_calendar.py    同步交易日历
```

## 快速开始

```bash
# 安装依赖
pip install -e ".[dev]"

# 配置（复制模板，填入 token）
cp config/default.yaml config/local.yaml
cp .env.example .env
# 编辑 .env，填写 TUSHARE_TOKEN 或留空（使用 baostock 免费源）

# 下载数据（示例：贵州茅台 近3年）
python scripts/download_data.py --symbols 600519.SH --years 3

# 运行回测
python scripts/run_backtest.py \
  --strategy double_ma \
  --symbols 600519.SH,000001.SZ \
  --start 2022-01-01 \
  --end 2024-12-31

# 运行测试
pytest tests/unit/           # 快速，无网络依赖
pytest tests/integration/    # 需要本地数据
```

## 核心概念

### 事件流

```
DataFeed
  └─ BarEvent ──────────────────────────────────────────────┐
                                                             ▼
Strategy.on_bar()                                      EventBus（优先队列）
  └─ emit(SignalEvent) ─────────────────────────────────────┤
                                                             ▼
PreTradeRisk.check()                              priority 顺序处理：
  ├─ 通过 → emit(OrderEvent)                      10 MARKET_DATA
  └─ 拒绝 → emit(RejectEvent)                     20 SIGNAL
                                                  30 ORDER
BarMatchingEngine.match()                         40 FILL
  └─ D+1 开盘价成交 → emit(FillEvent)             90 EOD
                                                             │
PortfolioManager.on_fill()                                   ▼
  └─ 更新持仓（T+1 锁定）                       每日 settle_eod()
                                                  解锁当日买入
```

### T+1 交割

A 股标准：当日买入的股票，**次日**才可卖出。

`Position` 有两个数量字段：
- `total_qty`：总持仓（当日买入立即更新）
- `tradeable_qty`：可卖数量（当日买入不增加，EOD 后解锁）

撮合引擎和风控双重保证不会绕过此约束。

### 回测无前视偏差

策略在 **D 日**产生信号 → 订单在 **D+1 日开盘**成交。
`BarMatchingEngine` 持有待处理订单队列，每日 `before_trading` 时才对前一日订单撮合。

### 策略代码不感知环境

策略只通过 `StrategyContext` 只读访问账户状态，通过 `self.buy()` / `self.sell()` 发出信号。
引擎负责将信号路由到模拟撮合引擎（回测）或券商 API（实盘）。

## 文档

| 文档 | 说明 |
|------|------|
| [架构总览](docs/ARCHITECTURE.md) | 模块关系、事件流、关键设计决策 |
| [数据层设计](docs/design/01-data-layer.md) | DataSource / DataStore / DataFeed / 复权 |
| [回测引擎设计](docs/design/02-backtest-engine.md) | 撮合逻辑、T+1、涨跌停 |
| [策略开发指南](docs/design/03-strategy-guide.md) | 如何编写策略、可用接口 |
| [风控设计](docs/design/04-risk-management.md) | 下单前检查、组合风控 |
| [绩效指标](docs/design/05-performance-metrics.md) | 各指标的计算公式和实现 |

## 开发路线

- [x] 项目初始化 + 文档
- [ ] **Phase 1**：数据层（core models、事件总线、ParquetStore、BaostockSource、复权、交易日历）
- [ ] **Phase 2**：回测引擎（撮合引擎、投资组合管理、风控、回测主循环、绩效指标）
- [ ] **Phase 3**：Web 监控面板
- [ ] **Phase 4**：实盘接口（QMT / xtquant）

## 依赖

| 库 | 用途 |
|----|------|
| `pandas` | 数据处理 |
| `pyarrow` | Parquet 读写 |
| `pydantic` | 配置校验 |
| `loguru` | 日志 |
| `baostock` | 免费 A 股数据源 |
| `tushare` | 付费 A 股数据源（可选）|
| `pytest` + `hypothesis` | 测试 |
| `pandas-ta` | 技术指标（替代手写实现）|
