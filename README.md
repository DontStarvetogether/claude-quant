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
├── research/           因子分层、IC、因子报告
├── benchmark/          标准 benchmark 和平台交叉验证
├── universe/           静态、流动性、PIT 股票池
├── live/               实盘安全、恢复、日报、报警
└── utils/              日志、配置、A股规则工具

tests/                  测试套件
├── unit/               单元测试（纯内存，无外部依赖）
├── integration/        集成测试（完整流程验证）
└── property/           属性测试（hypothesis，验证数学不变式）

scripts/                兼容旧用法的脚本入口
web/                    FastAPI + 静态 Web 页面
```

## 快速开始

需要 Python `3.11` 或 `3.12`。

```bash
# 安装依赖
pip install -e ".[dev]"

# 如果要在自定义策略里使用 pandas-ta 技术指标
pip install -e ".[dev,indicators]"

# 配置（复制模板，填入 token）
cp config/default.yaml config/local.yaml
cp .env.example .env
# 编辑 .env，填写 TUSHARE_TOKEN 或留空（使用 baostock 免费源）

# 查看统一 CLI
cq --help

# 下载数据（示例：贵州茅台 近3年）
cq download-data --symbols 600519.SH --years 3

# 运行回测
cq backtest \
  --strategy double_ma \
  --symbols 600519.SH \
  --symbols 000001.SZ \
  --start 2022-01-01 \
  --end 2024-12-31

# 标准化 point-in-time 指数成分股文件
cq import-pit-universe \
  --input data/external/hs300_membership.csv \
  --output data/universes/pit_memberships.csv

# 从 AkShare 免费公开源下载三大宽基最新成分快照（best-effort，不等同严格历史 PIT）
cq fetch-pit-universe \
  --provider akshare \
  --start 2024-01-01 \
  --end 2026-05-23 \
  --output data/universes/pit_memberships.csv \
  --weights-output data/universes/pit_weights.csv \
  --raw-dir data/raw/akshare/csindex \
  --validation-dir output/universe_validation
# 关键诊断会写入 output/universe_validation/pit_fetch_summary.json
# 人工复核报告会写入 output/universe_validation/pit_fetch_report.md
# 同名 sidecar 会写入 data/universes/pit_memberships.summary.json，benchmark 会自动带入来源诊断

# 从 Tushare Pro 下载三大宽基历史 PIT 成分股（需要 TUSHARE_TOKEN）
cq fetch-pit-universe \
  --provider tushare \
  --start 2015-01-01 \
  --end 2026-05-23 \
  --output data/universes/pit_memberships.csv \
  --weights-output data/universes/pit_weights.csv \
  --raw-dir data/raw/tushare/index_weight \
  --validation-dir output/universe_validation

# 校验 PIT 文件是否覆盖预期股票池和日期
cq validate-pit-universe \
  --input data/universes/pit_memberships.csv \
  --expected-universe HS300_PIT \
  --min-symbols 250 \
  --coverage-start 2020-01-01 \
  --coverage-end 2024-12-31 \
  --output-dir output/universe_validation/hs300

# 运行标准 benchmark 实验包
cq benchmark \
  --price-csv output/prices.csv \
  --pit-csv data/universes/pit_memberships.csv \
  --universe-id HS300_PIT \
  --output-dir output/benchmark/hs300

# 生成外部平台对账模板
cq cross-validation-template \
  --platform-name JoinQuant \
  --output-dir output/benchmark/joinquant_template

# 平台交叉验证
cq cross-validate \
  --local-dir output/benchmark/hs300 \
  --external-dir output/benchmark/joinquant \
  --output-dir output/cross_validation/joinquant \
  --platform-name JoinQuant

# 运行测试
pytest tests/unit/           # 快速，无网络依赖
pytest tests/integration/
ruff check cq web/routers scripts
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
| [PIT 股票池数据源](docs/data_sources/pit_universe_sources.md) | Tushare/AkShare/JoinQuant/RiceQuant 数据源适用性 |

## 开发路线

- [x] 项目初始化 + 文档
- [x] **Phase 1**：数据层（core models、事件总线、ParquetStore、Akshare/BaostockSource、复权、交易日历）
- [x] **Phase 2**：回测引擎（撮合引擎、投资组合管理、风控、回测主循环、绩效指标）
- [x] **Phase 3**：Web 回测 / 模拟盘面板
- [ ] **Phase 4**：准实盘安全闭环（QMT / xtquant、人工确认、报警、外部平台对账）

## 依赖

| 库 | 用途 |
|----|------|
| `pandas` | 数据处理 |
| `pyarrow` | Parquet 读写 |
| `pydantic` | 配置校验 |
| `loguru` | 日志 |
| `akshare` | 默认免费 A 股数据源 |
| `baostock` | 备用免费 A 股数据源 |
| `pytest` + `hypothesis` | 测试 |
| `pandas-ta` | 技术指标（可选，安装 `.[indicators]`）|
