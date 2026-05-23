# Claude-Quant 优化计划

> 维护规则：本文件是项目优化的单一事实来源。每次完成优化、修复或调整优先级后，都要同步更新“当前进度”和对应 Phase 的状态，避免只依赖对话记忆。
> 最近更新：2026-05-23

---

## 当前进度

| 模块 | 状态 | 证据 / 备注 |
|---|---|---|
| Phase 0 项目基线整理 | 已完成初版 | Python 已锁定 `>=3.11,<3.13`；GitHub Actions 已跑 `tests/unit tests/integration`；`pandas-ta` 已移入可选依赖；新增 `docs/dev_guide.md`；`.gitignore` 已覆盖常见产物 |
| Phase 1.1 T+1 卖出语义 | 已完成初版 | `PreTradeRisk` 信号日按 `total_qty` 允许生成次日卖单，撮合日再由 `BarMatchingEngine` 按 `tradeable_qty` 最终检查；已有集成测试覆盖 D+1 买入后 D+2 可卖，并纳入 CI |
| Phase 1.2 A 股涨跌停规则 | 已完成初版 | 已集中到 `cq/utils/trading_rules.py::AStockRules`，覆盖主板、ST、创业板、科创板、北交所；`HistoricalFeed` 缺涨跌停字段时也用统一规则从 `pre_close` 计算 |
| Phase 1.3 成交拒单诊断 | 已完成初版 | 回测结果已输出 `execution_diagnostics`，结果页展示拒单分类、成交比例、容量限制等 |
| Phase 1.4 撮合当日行情隔离 | 已完成初版 | `BarMatchingEngine` 已按交易日清空 bar 缓存，并在撮合时校验 `bar.trade_date == today`，防止缺失行情时误用旧 bar 成交 |
| Phase 2.1 换手率指标 | 已完成初版 | `PerformanceMetrics` 已输出日均/年化/最大/买入/卖出换手率，结果页已展示 |
| Phase 2.2 成本前后收益 | 已完成初版 | 已输出 `gross_return/net_return/gross_annual_return/net_annual_return/cost_drag/cost_to_nav` 等 |
| Phase 2.3 组合暴露指标 | 已完成初版 | 已输出平均持仓数、现金占比、最大单票、Top5 集中度；行业/市值暴露未做 |
| Web 回测/模拟盘展示对齐 | 已完成一轮 | 回测、模拟盘、策略对比页颜色语义统一；模拟盘成交记录补持有现金、公司名称、唯一 session URL、图上买卖标记 |
| Phase 3 因子研究模块 | 已完成初版 | 已新增 `cq/research`：Forward Return、Rank IC、因子分层、Top-Bottom、覆盖率、分组换手、Markdown 因子报告，并支持 CSV/JSON/Markdown 标准导出；`scripts/run_factor_report.py` 可从 CSV 生成报告 |
| Phase 4 标准 Benchmark 策略 | 已完成初版 | 已新增独立 `cq/benchmark`，支持 20日动量 TopN；输出信号、每日净值、持仓、成交，并支持 CSV/JSON/Markdown 标准导出与回测字段映射 |
| Phase 5 股票池体系升级 | 已完成初版 | 已新增 `cq/universe`、静态股票池、`ALL_A_LIQUID` 动态流动性池和 `PointInTimeUniverseProvider`；PIT 可从 CSV/DataFrame 按日期解析，真实指数历史成分股数据接入待做 |
| Phase 6 平台交叉验证 | 已完成增强版初版 | 已新增 `cq/benchmark/cross_validation.py`、`scripts/run_cross_validation.py` 和 `docs/cross_validation_report.md`；可从本地/外部平台 CSV 直接加载、标准化常见字段别名、比较每日净值/持仓/成交并导出差异报告；真实外部平台样本对账待执行 |
| Phase 7 模拟盘 / 实盘安全层 | 已完成增强版初版 | 模拟盘已有会话持久化和历史查看；已新增订单幂等、交易计划确认、风控总开关、单日亏损守卫、重启恢复状态、每日交易日报和异常报警通道；安全层已接入 `LiveEngine` 和 `paper_trade` 信号入口；真实通知渠道和 Web 配置入口仍待做 |

## 下一步优化动作

当前建议不要继续堆 UI 细节，优先进入“研究能力 + 可回归验证”：

1. **Phase 4 继续：完善 Benchmark 结果消费链路**
   - `20日动量 TopN` 第一版已完成：调仓日收盘选股、下一交易日开盘成交
   - 已补齐 CSV/JSON/Markdown 导出、标准摘要和与现有回测结果页的字段映射
   - 已补交叉验证对账工具和 CLI；后续拿到真实外部平台导出样本后，直接执行对账并做差异归因

2. **Phase 5 补股票池抽象**
   - 第一版 `UniverseProvider` 已完成，静态预设已从 Web 层下沉到核心包
   - `ALL_A_LIQUID` 初版已完成：按交易日从日线数据筛选非 ST、非停牌、足够上市天数、足够成交额、价格正常、无长期零成交的动态股票池，并输出逐股票诊断
   - 已补 `ParquetStore.list_symbols()` 和 `StoreBackedLiquidUniverseProvider`，可直接从本地 qfq/raw bars 构建动态流动性池
   - 已补 `PointInTimeUniverseProvider`，可从 CSV/DataFrame 解析历史成分股生效区间
   - 下一步接真实 HS300/ZZ500/ZZ1000 历史成分股数据文件

3. **Phase 3 后续增强**
   - CSV/JSON/Markdown 导出已完成初版
   - 已新增 `scripts/run_factor_report.py`，可从因子 CSV + 价格 CSV 生成报告目录
   - 接入 Web 页面前先用 benchmark 和交叉验证结果继续验证口径

## 进度更新约定

- 新增功能完成后，把对应任务从“未开始/进行中”更新为“已完成初版”或“已完成”。
- 如果实现和原计划有偏差，要在对应 Phase 下补“实际实现说明”。
- 如果发现新风险，写入“下一步优化动作”，不要只留在聊天记录里。
- 提交代码前检查本文件是否需要同步更新。

---

> 适用仓库：`https://github.com/DontStarvetogether/claude-quant`  
> 当前定位：A 股中低频事件驱动回测 / 研究框架  
> 优化目标：从“学习型 / 半研究型框架”升级为“可信的 A 股中低频量化研究框架”，并为后续模拟盘和小资金实盘验证打基础。

---

## 0. 总体判断

当前 `claude-quant` 已经不是简单脚本，而是一个有较清晰架构的 A 股量化框架雏形。

已有较好的基础：

- 事件驱动架构
- 回测引擎
- 撮合模块
- 组合账户模块
- 风控模块
- 数据层
- 策略接口
- Web 层
- 测试目录
- Paper / QMT / Simulated execution 相关模块

但目前最关键的问题不是继续堆策略，而是先提高：

1. 回测可信度
2. A 股撮合规则严谨性
3. 绩效指标完整性
4. 因子研究能力
5. 结果可复现与平台交叉验证能力

---

## 1. 当前评级

| 维度 | 当前评级 | 目标评级 | 说明 |
|---|---:|---:|---|
| 工程结构 | Level 3.5 / 5 | Level 4 / 5 | 模块已经比较完整，继续规范化即可 |
| A 股撮合可信度 | Level 2.8 / 5 | Level 3.8 / 5 | 需要修 T+1、涨跌停、拒单诊断 |
| 策略回测能力 | Level 3 / 5 | Level 4 / 5 | 已能跑策略，但需要标准 benchmark |
| 因子研究能力 | Level 2 / 5 | Level 3.5 / 5 | 缺单因子分层、IC、因子报告 |
| 数据处理能力 | Level 3 / 5 | Level 3.8 / 5 | 需要加强股票池、历史成分股、ST、停牌 |
| 模拟 / 实盘能力 | Level 2 / 5 | Level 3 / 5 | 可以保留，但暂时不要全自动实盘 |

---

## 2. 总体路线

建议按照以下路线推进：

```text
修撮合可信度
→ 补绩效指标
→ 补因子研究模块
→ 建标准 benchmark 策略
→ 优化股票池体系
→ 和成熟平台交叉验证
→ 再考虑模拟盘 / 实盘
```

当前最重要的原则：

> 不要急着新增复杂策略，也不要急着上实盘。  
> 先保证每一条回测结果可信、可解释、可复现。

---

# Phase 0：项目基线整理

## 目标

让项目可以被稳定安装、运行、测试和复现。

## 任务清单

| 任务 | 优先级 | 说明 | 验收标准 |
|---|---:|---|---|
| 明确 Python 版本 | P0 | 建议锁定 `>=3.11,<3.13` | README / pyproject 明确说明 |
| 整理依赖 | P0 | 区分 runtime / dev / optional 依赖 | `pip install -e ".[dev]"` 可用 |
| 增加 CI | P0 | GitHub Actions 自动跑测试 | push 后自动执行 unit tests |
| 清理运行产物 | P1 | `.venv/`, `*.db`, `__pycache__`, `.DS_Store` 不入库 | `.gitignore` 覆盖完整 |
| README 增加最小运行例子 | P1 | 新用户能快速跑一个回测 | 5 分钟内跑通 demo |
| 增加开发说明 | P2 | 贡献规范、测试说明 | `docs/dev_guide.md` |

## 建议命令

```bash
pip install -e ".[dev]"
pytest tests/unit
pytest tests/integration
```

---

# Phase 1：修正撮合可信度

这是最高优先级阶段。  
如果撮合语义不正确，所有策略结果都不可信。

---

## 1.1 修正 T+1 卖出语义

### 当前风险

当前框架设计为：

```text
D 日生成信号
D+1 开盘撮合
D+1 收盘 EOD 处理
```

这个方向是对的。

但需要重点检查卖出逻辑：

```text
Day 1 收盘产生买入信号
Day 2 开盘买入成交
Day 2 收盘产生卖出信号
Day 3 开盘理论上应该可以卖出
```

如果 `PreTradeRisk` 在 Day 2 收盘生成卖单时使用 `tradeable_qty` 检查，可能会拒绝卖出信号，因为 Day 2 买入的股票此时尚未解锁为可卖。

这样会导致实际变成：

```text
Day 4 才能卖
```

这会影响：

- 退出速度
- 最大回撤
- 止损有效性
- 持仓周期
- 交易次数
- 策略收益

### 建议修改

把卖出检查拆成两层：

| 阶段 | 检查内容 |
|---|---|
| 信号生成日 | 检查 `total_qty` 是否足够，可以生成次日卖单 |
| 真实撮合日 | 检查 `tradeable_qty` 是否足够，决定是否成交 |

也就是：

```text
信号日允许生成明日卖单
成交日再做最终可卖数量检查
```

### 可能改动文件

```text
cq/risk/pre_trade.py
cq/engine/backtest_engine.py
cq/engine/matching.py
cq/core/models.py
```

### 验收测试

新增测试：

```text
Day 1 收盘买入信号
Day 2 开盘买入成交
Day 2 收盘卖出信号
Day 3 开盘卖出成功
```

该测试必须通过。

---

## 1.2 统一 A 股涨跌停规则

### 当前需要统一的规则

| 类型 | 涨跌幅限制 |
|---|---:|
| 主板普通股票 | 10% |
| 主板 ST / *ST | 5% |
| 创业板 | 20% |
| 科创板 | 20% |
| 北交所 | 30% |

### 建议新增统一入口

```python
class AStockRules:
    @staticmethod
    def get_limit_pct(
        symbol: str,
        is_st: bool = False,
        trade_date: date | None = None,
    ) -> float:
        ...
```

### 所有模块统一调用

```text
Bar 构造
撮合判断
风控判断
涨跌停过滤
策略过滤
数据 fallback
测试用例
```

不要在多个地方重复写板块判断逻辑。

### 验收测试

至少覆盖：

```text
600000.SH → 10%
000001.SZ → 10%
300750.SZ → 20%
688981.SH → 20%
430047.BJ → 30%
主板 ST → 5%
创业板 ST → 20%
科创板 ST → 20%
```

---

## 1.3 增加成交拒单诊断

### 目标

每一笔未成交订单都应该知道原因。

### 建议增加字段

| 诊断项 | 说明 |
|---|---|
| `rejected_by_limit_up` | 涨停买不进 |
| `rejected_by_limit_down` | 跌停卖不出 |
| `rejected_by_suspended` | 停牌无法成交 |
| `rejected_by_cash` | 现金不足 |
| `rejected_by_t1` | T+1 限制 |
| `rejected_by_position` | 持仓不足 |
| `capacity_limited_fills` | 成交量限制导致缩量 |
| `partial_fill_ratio` | 平均成交比例 |

### 验收标准

回测结果 summary 中能看到：

```text
订单总数
成交订单数
拒单订单数
涨停拒单数
跌停拒单数
T+1 拒单数
现金不足拒单数
部分成交比例
```

---

# Phase 2：补全绩效分析

## 目标

从“能看收益回撤”升级为“能判断策略是否可交易”。

---

## 2.1 新增换手率指标

### 为什么重要

很多策略回测收益高，但换手率过高，最终会被：

```text
手续费 + 印花税 + 滑点 + 冲击成本
```

完全吃掉。

### 指标清单

| 指标 | 优先级 | 说明 |
|---|---:|---|
| `daily_turnover` | P0 | 当日成交金额 / 当日总资产 |
| `avg_daily_turnover` | P0 | 平均日换手 |
| `annual_turnover` | P0 | 年化换手 |
| `max_daily_turnover` | P1 | 最大单日换手 |
| `buy_turnover` | P1 | 买入换手 |
| `sell_turnover` | P1 | 卖出换手 |
| `cost_to_nav` | P1 | 总交易成本 / 平均资产规模 |

### 初版计算方式

```text
日换手率 = 当日成交金额 / 当日总资产
```

后续可增加权重版本：

```text
换手率 = sum(abs(target_weight - current_weight)) / 2
```

---

## 2.2 增加成本前后收益

### 建议指标

| 指标 | 含义 |
|---|---|
| `gross_return` | 成本前收益 |
| `net_return` | 成本后收益 |
| `total_commission` | 总佣金 |
| `total_stamp_tax` | 总印花税 |
| `total_slippage_cost` | 总滑点成本 |
| `cost_drag` | 成本拖累 |

### 验收标准

每次回测必须输出：

```text
成本前年化收益
成本后年化收益
交易成本拖累
总费用
总滑点成本
```

---

## 2.3 增加组合暴露指标

### 建议指标

| 指标 | 优先级 |
|---|---:|
| 平均持仓数量 | P1 |
| 最大持仓数量 | P1 |
| 最小持仓数量 | P1 |
| 平均现金占比 | P1 |
| 最大单票权重 | P1 |
| Top5 持仓集中度 | P2 |
| 行业暴露 | P2 |
| 市值暴露 | P2 |

---

# Phase 3：新增因子研究模块

## 目标

让框架从“策略回测框架”升级为“量化研究框架”。

当前最缺的是：

```text
单因子分层
IC 分析
Top-Bottom 收益
因子报告
```

---

## 3.1 建议新增目录

```text
cq/research/
  __init__.py
  factor.py
  forward_return.py
  grouping.py
  ic.py
  report.py
```

---

## 3.2 统一输入格式

建议因子输入采用长表：

| date | symbol | factor |
|---|---|---:|

价格输入：

| date | symbol | close |
|---|---|---:|

输出中增加：

| date | symbol | factor | forward_return_1d | forward_return_5d | forward_return_20d |
|---|---|---:|---:|---:|---:|

---

## 3.3 实现 Forward Return

### API 建议

```python
calculate_forward_returns(
    price_df,
    periods=[1, 5, 20],
    price_col="close",
)
```

### 注意事项

- 必须按 symbol 分组计算
- `shift(-n)` 只能作为标签，不能进入交易信号
- 需要处理停牌和缺失价格
- 需要保证日期对齐

---

## 3.4 实现因子分层

### API 建议

```python
analyze_factor_groups(
    factor_df,
    forward_return_df,
    group_count=5,
    periods=[1, 5, 20],
)
```

### 输出

| 输出 | 说明 |
|---|---|
| `group_return` | 每组平均未来收益 |
| `group_nav` | 每组净值曲线 |
| `top_bottom_return` | 高分组 - 低分组 |
| `monotonicity` | 分层是否单调 |
| `coverage` | 因子覆盖率 |
| `turnover_by_group` | 分组换手率 |

---

## 3.5 实现 IC 分析

### 优先实现 Rank IC

```python
from scipy.stats import spearmanr

ic = spearmanr(factor_values, forward_returns).correlation
```

### 指标

| 指标 | 说明 |
|---|---|
| `ic_mean` | IC 均值 |
| `ic_std` | IC 标准差 |
| `icir` | IC 均值 / IC 标准差 |
| `ic_win_rate` | IC 为正比例 |
| `rank_ic` | Spearman IC |
| `pearson_ic` | Pearson IC，可后续做 |

---

## 3.6 实现因子报告

### 报告内容

```text
因子名称
股票池
测试区间
样本数量
覆盖率
IC Mean
IC Std
ICIR
IC Win Rate
Top 组收益
Bottom 组收益
Top-Bottom 收益
分层是否单调
平均换手率
最大回撤
```

### 建议输出格式

- Markdown
- CSV
- JSON

第一版已支持 Markdown、CSV、JSON：

```text
export_factor_report()
scripts/run_factor_report.py
coverage.csv
ic_summary.csv
group_return.csv
group_nav.csv
top_bottom_return.csv
monotonicity.csv
turnover_by_group.csv
summary.json
report.md
```

---

## 3.7 第一批测试因子

先不要上复杂因子。

建议只测：

| 因子 | 公式 | 类型 |
|---|---|---|
| 20日动量 | `close / close.shift(20) - 1` | 动量 |
| 均线趋势 | `MA20 / MA60 - 1` | 趋势 |
| 20日波动率 | `return.rolling(20).std()` | 风险 |
| 5日反转 | `-pct_change(5)` | 反转 |

---

# Phase 4：建立标准 Benchmark 策略

## 目标

以后任何框架改动，都可以和标准策略结果对比，防止改坏核心逻辑。

---

## 4.1 Benchmark 1：双均线择时

### 用途

测试基本交易流程。

### 规则

```text
股票池：沪深300
信号：MA20 > MA60 持有，否则空仓
成交：T+1 开盘
调仓：每日或每周
权重：等权
```

### 检查点

- 信号生成是否正确
- T+1 是否正确
- 成交价格是否正确
- 持仓是否正确
- 净值是否正确

---

## 4.2 Benchmark 2：20日动量 Top20

### 用途

测试横截面选股能力。

### 规则

```text
股票池：沪深300
因子：close / close.shift(20) - 1
调仓：每周
持股：Top20
权重：等权
成交：T+1 开盘
```

### 输出

```text
净值曲线
年化收益
最大回撤
夏普
日换手率
年化换手率
成本前收益
成本后收益
每日持仓
每日成交
```

---

## 4.3 Benchmark 3：20日动量 + 缓冲区

### 用途

测试换手控制。

### 规则

```text
目标持股：20 只
买入：排名进入前20
卖出：排名跌出前60
调仓：每周
权重：等权
```

### 对比目标

与 Benchmark 2 对比：

| 指标 | 期望 |
|---|---|
| 换手率 | 明显下降 |
| 成本拖累 | 明显下降 |
| 收益 | 不应显著恶化 |
| 回撤 | 不应显著恶化 |

---

# Phase 5：股票池体系升级

## 目标

减少幸存者偏差和流动性幻觉。

---

## 5.1 新增 UniverseProvider

### API 建议

```python
class UniverseProvider:
    def get_symbols(self, trade_date) -> list[str]:
        ...
```

### 支持类型

| 类型 | 说明 |
|---|---|
| 静态股票池 | 用于快速测试 |
| 历史成分股 | 用于严肃回测 |
| 全 A 流动性池 | 用于实盘可交易研究 |
| 行业内股票池 | 用于行业中性研究 |
| 自定义股票池 | 用于用户手动配置 |

---

## 5.2 第一批股票池

| 股票池 | 优先级 | 用途 |
|---|---:|---|
| `HS300_STATIC` | P0 | 快速验证 |
| `ZZ500_STATIC` | P0 | 中盘验证 |
| `ZZ1000_STATIC` | P1 | 小盘验证 |
| `ALL_A_LIQUID` | P1 | 实盘可交易池 |
| `HS300_PIT` | P2 | 历史沪深300成分股 |
| `ZZ500_PIT` | P2 | 历史中证500成分股 |
| `ZZ1000_PIT` | P2 | 历史中证1000成分股 |

---

## 5.3 ALL_A_LIQUID 建议规则

```text
剔除 ST / *ST
剔除上市不足 120 或 250 个交易日
剔除停牌股票
剔除过去20日平均成交额低于 5000万 / 1亿的股票
剔除价格异常股票
剔除退市整理股票
剔除长期无成交股票
```

### 当前实现说明

已新增 `cq/universe/liquid.py`：

```text
LiquidUniverseConfig
LiquidUniverseSelection
LiquidUniverseProvider
select_all_a_liquid_universe()
build_all_a_liquid_universe()
```

当前规则按指定 `trade_date` 在输入日线数据中筛选：

```text
必须有当日 bar，避免误用旧行情
默认剔除 ST、停牌、上市交易日不足 120 日
默认剔除过去 20 日平均成交额低于 5000 万
默认剔除价格异常、过去窗口有零成交的股票
可用 top_n 按平均成交额截取更高流动性子集
输出 diagnostics，标明每只股票入选或被剔除的原因
```

后续仍需：

```text
已接入 ParquetStore，自动从本地全量候选股票读取 bars
补退市整理 / 当前股票名称状态的 point-in-time 支持
已补 PointInTimeUniverseProvider 通用能力
补 HS300_PIT / ZZ500_PIT / ZZ1000_PIT 真实历史成分股数据源
```

---

# Phase 6：平台交叉验证

## 目标

确认 `claude-quant` 的回测结果不是自嗨。

---

## 6.1 选择对照策略

建议使用：

```text
沪深300
20日动量
每周调仓
前20等权
T+1 开盘成交
手续费 + 印花税 + 滑点
```

---

## 6.2 对照平台

任选一个或多个：

```text
聚宽
米筐
掘金
QMT 模拟盘
```

---

## 6.3 对比内容

不要只看最终收益。

必须对比：

| 项目 | 是否必须 |
|---|---:|
| 每日净值 | 必须 |
| 每日持仓 | 必须 |
| 每日成交 | 必须 |
| 成交价格 | 必须 |
| 手续费 | 必须 |
| 印花税 | 必须 |
| 滑点 | 必须 |
| 换手率 | 必须 |
| 最大回撤 | 必须 |
| 涨跌停拒单 | 必须 |
| 停牌处理 | 必须 |

---

## 6.4 差异排查顺序

如果结果不同，优先检查：

```text
复权方式
股票池是否 point-in-time
调仓日是否一致
信号日和成交日是否一致
成交价格是否一致
手续费和印花税是否一致
滑点是否一致
涨跌停处理是否一致
停牌处理是否一致
新股 / 退市 / ST 处理是否一致
```

## 6.5 当前实现说明

已新增 `cq/benchmark/cross_validation.py`：

```text
load_cross_validation_frames()
compare_benchmark_with_external()
generate_cross_validation_report()
export_cross_validation_result()
CrossValidationInputFiles
CrossValidationTolerance
```

支持把本地 benchmark 输出和外部平台 DataFrame / CSV 规范化后比较，并已支持常见平台导出字段别名：

```text
每日净值：date,total_assets,cash,position_value
每日持仓：date,symbol,quantity,market_value
每日成交：trade_date,symbol,side,quantity,price,amount,commission,stamp_tax,net_amount
```

已新增 CLI：

```bash
python scripts/run_cross_validation.py \
  --local-dir output/benchmark/local \
  --external-dir output/benchmark/joinquant \
  --output-dir output/cross_validation/joinquant \
  --platform-name JoinQuant
```

支持两种输入方式：

```text
目录方式：默认读取 equity_curve.csv / holdings.csv / trades.csv
显式文件方式：分别传入 --local-equity-csv、--external-trades-csv 等
```

输出：

```text
cross_validation_summary.json
cross_validation_report.md
equity_comparison.csv
holdings_comparison.csv
trades_comparison.csv
```

真实外部平台对账仍待执行，后续需要准备一组聚宽/米筐/QMT 的同策略导出样本，然后用 CLI 固化差异报告。

---

# Phase 7：模拟盘 / 实盘安全层

## 目标

在回测可信后，逐步进入模拟盘，不直接全自动实盘。

---

## 7.1 推荐执行路径

```text
策略信号
→ 生成交易计划
→ 人工确认
→ 模拟盘下单
→ 小资金人工确认实盘
→ 再考虑自动化
```

不要直接：

```text
策略信号
→ 自动真实下单
```

---

## 7.2 实盘前必须补齐

| 能力 | 优先级 | 说明 |
|---|---:|---|
| 账户持仓同步 | P0 | 和券商账户一致 |
| 订单幂等 | P0 | 避免重复下单 |
| 重启恢复 | P0 | 程序崩溃后状态不丢 |
| 风控总开关 | P0 | 紧急停止 |
| 单日最大亏损限制 | P0 | 防止失控 |
| 单票最大仓位 | P0 | 控制集中度 |
| 下单前二次确认 | P0 | 初期必须人工确认 |
| 每日交易日报 | P1 | 复盘和审计 |
| 异常报警 | P1 | 网络、行情、接口异常 |

## 7.3 当前实现说明

已新增 `cq/live/safety.py`：

```text
OrderIntent
OrderIdempotencyStore
TradePlan
KillSwitch
DailyLossGuard
SafetyCheckResult
```

已新增 `cq/live/report.py`：

```text
generate_daily_trading_report()
export_daily_trading_report()
DailyTradingReport
```

已新增 `cq/live/alerts.py`：

```text
AlertEvent
AlertManager
InMemoryAlertSink
JsonlAlertSink
```

已新增 `cq/live/recovery.py`：

```text
LiveRecoveryState
LiveRecoveryStore
```

当前完成：

```text
订单意图可生成稳定 idempotency key
幂等 key 可内存保存或 JSON 持久化
TradePlan 支持 pending / approved / rejected 人工确认状态
KillSwitch 可统一阻断新订单
DailyLossGuard 可按单日亏损金额或比例阻断交易
PaperExecutor 已接入订单幂等拦截
SimulatedExecutor 已接入订单幂等拦截，供 paper_trade / 回测式模拟复用
QMTExecutor 已预留同一套订单幂等入口
LiveEngine.configure_safety() 已接入 KillSwitch / DailyLossGuard / OrderIdempotencyStore
LiveEngine 会在 SignalEvent 进入执行器前做安全检查，拦截后生成 RejectEvent
LiveEngine.run() 会把 idempotency store 传入 QMTExecutor
LiveEngine.paper_trade() 会把 idempotency store 传入 SimulatedExecutor
每日交易日报可从成交、权益曲线、持仓、风险提示生成 Markdown/JSON/CSV
异常报警可发送到内存 sink 或 JSONL 文件，后续可扩展邮件/企业微信/飞书
重启恢复状态可保存/加载 session 状态、幂等 key、待审批交易计划 id
```

后续仍需：

```text
将 TradePlan 接入 Web 实盘启动和下单前确认流程
将 LiveRecoveryStore 接入 Web / LiveEngine 启动恢复流程
将每日交易日报接入 Web / 定时任务
将异常报警接入具体通知渠道和 LiveEngine 异常路径
```

---

# 建议 GitHub Issues

建议直接在 GitHub 开这些 Issue：

| Issue | 标题 | 优先级 |
|---:|---|---:|
| 1 | Fix T+1 sell order semantics for next-day execution | P0 |
| 2 | Centralize A-share board-specific price limit rules | P0 |
| 3 | Add turnover metrics to performance module | P0 |
| 4 | Add execution rejection diagnostics summary | P1 |
| 5 | Add gross vs net return metrics | P1 |
| 6 | Add single-factor analysis module: grouping + Rank IC | P1 |
| 7 | Add factor report generation in Markdown | P1 |
| 8 | Add benchmark strategy: weekly 20d momentum Top20 | P1 |
| 9 | Add benchmark strategy with rank buffer | P1 |
| 10 | Add UniverseProvider abstraction | P1 |
| 11 | Add ALL_A_LIQUID universe | P1 |
| 12 | Add GitHub Actions CI for Python 3.11 / 3.12 | P1 |
| 13 | Add platform cross-validation report template | P2 |
| 14 | Add paper trading safety checklist | P2 |

---

# 近期执行顺序

## Week 1：修可信度

```text
1. 修 T+1 卖出语义
2. 统一涨跌停规则
3. 增加相关单元测试
4. 增加换手率指标
5. 增加成本前后收益指标
```

验收标准：

```text
D+1 买入，D+2 可卖
创业板 / 科创板 / 北交所涨跌停正确
绩效报告中有换手率
绩效报告中有成本前后收益
```

---

## Week 2：补研究模块

```text
1. 新建 cq/research
2. 实现 forward return
3. 实现因子分层
4. 实现 Rank IC
5. 生成 Markdown 因子报告
```

验收标准：

```text
输入一个 20日动量因子
可以输出：
- 分组收益
- Top-Bottom 收益
- IC Mean
- ICIR
- 因子覆盖率
```

---

## Week 3：建立 benchmark

```text
1. 双均线择时 baseline
2. 20日动量 Top20
3. 20日动量 + 缓冲区
4. 输出每日持仓和每日成交
```

验收标准：

```text
每个 benchmark 都能稳定跑完
结果可导出 CSV / Markdown
能比较换手率和成本拖累
```

---

## Week 4：平台交叉验证

```text
1. 在聚宽 / 米筐 / 掘金复现同策略
2. 对比每日净值
3. 对比每日持仓
4. 对比每日成交
5. 记录差异原因
```

验收标准：

```text
形成一份 cross_validation_report.md
清楚列出差异来源
修正 claude-quant 中明显不合理的差异
```

---

## Week 5+：模拟盘安全层

```text
1. 生成交易计划而不是直接下单
2. 增加人工确认流程
3. 增加账户同步
4. 增加重启恢复
5. 增加风控总开关
```

---

# 暂时不要做的事

现阶段不要优先做：

| 暂时不要做 | 原因 |
|---|---|
| 复杂多因子模型 | 单因子研究模块还没补齐 |
| 机器学习 | 容易过拟合，且当前数据/验证体系还不够 |
| Tick 级撮合 | 日频撮合还没完全可信 |
| 全自动实盘 | 风控和恢复机制还不够 |
| 复杂行业中性 | 先把基础因子报告做出来 |
| 过度优化性能 | 当前可信度比速度更重要 |
| 新增大量策略 | 先把 benchmark 和研究工具做好 |

---

# 最短可执行路线

最推荐的最短路径：

```text
第 1 步：修 T+1 卖出语义
第 2 步：统一涨跌停规则
第 3 步：补换手率和成本前后收益
第 4 步：实现 20日动量 Top20 benchmark
第 5 步：实现单因子分层 + Rank IC
第 6 步：和成熟平台做结果对照
```

完成这 6 步后，`claude-quant` 就会从“能跑策略”明显升级为“能做严肃研究”。

---

# 最终目标

最终理想形态：

```text
可信数据
+ 正确撮合
+ 完整绩效
+ 因子研究
+ 标准 benchmark
+ 平台交叉验证
+ 安全模拟盘
```

也就是：

> 一个可解释、可复现、适合 A 股中低频研究的个人量化框架。
