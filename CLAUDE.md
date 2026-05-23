# 项目概览

## 目标
面向初学者的 A 股量化交易框架，支持策略回测和实盘交易。Python + FastAPI + 纯 HTML/JS 前端，端口 8888。

## 目录结构

```
cq/
  core/       models.py(Bar/Order/Trade/Position/Account/Signal), events.py, event_bus.py
  data/       source/(baostock,akshare), store/parquet_store.py, feed/(historical,realtime,replay).py, adjust/adjuster.py, calendar.py
  engine/     backtest_engine.py, portfolio.py, matching/bar_matching.py
  strategy/   base.py(Strategy ABC + StrategyContext), examples/(double_ma,rsi,bollinger,momentum).py
  execution/  simulated.py, qmt.py(QMT券商对接), paper.py(模拟执行器)
  live/       engine.py(实盘引擎，支持 run/paper_trade 两种模式)
  risk/       pre_trade.py
  performance/metrics.py

web/
  app.py          FastAPI 主入口
  routers/        backtest.py, strategy.py, symbols.py, data.py, live.py
  schemas.py      Pydantic 请求/响应模型
  serializers.py  结果序列化
  runner.py       回测任务运行器
  live_runner.py  模拟盘会话管理器
  store.py        回测记录内存存储
  static/
    index.html      回测配置页（主页）
    result.html     回测结果页
    strategies.html 策略库页
    data.html       数据管理页
    compare.html    策略对比页
    live.html       模拟盘监控页
    js/             main.js, api.js, result.js, live.js

scripts/    run_backtest.py, run_live.py, download_data.py, download_all_stocks.py
run_web.py  启动入口
```

## 已实现模块
- 事件驱动引擎：优先级队列（MARKET_DATA→SIGNAL→ORDER→FILL→EOD）
- 数据层：Baostock/AKShare，Parquet 存储，前复权，交易日历
- 回测引擎：D日信号→D+1开盘成交（无前视偏差），T+1结算，涨跌停拒单
- 风控：单股仓位上限、最低现金比例、T+1 校验
- 绩效：总收益/年化/最大回撤/夏普/索提诺/卡玛/胜率/盈亏比
- Web 回测：配置UI + 结果页（净值曲线/回撤图/成交明细），SSE 实时进度，/api/backtest|strategy|symbols
- Web 页面：strategies.html（策略库）、data.html（数据管理）、compare.html（策略对比）
- 实盘核心：QMT 券商对接（qmt.py）、实时行情（realtime.py）、实盘引擎（live/engine.py）、模拟执行器（paper.py）
- 策略示例：双均线、RSI、布林带、动量（4个）
- Web 模拟盘：live.html 监控页 + /api/live（start/stop/status/stream/sessions），SSE 实时推送持仓和成交

## 缺失模块（待实现）
- P0 项目基线：GitHub Actions CI、Python 版本上限、开发说明
- P1 研究能力：`cq/research` 单因子分层、Forward Return、Rank IC、Markdown 因子报告
- P1 Benchmark：标准 20 日动量 Top20、动量缓冲区、每日持仓/成交导出
- P1 股票池：UniverseProvider、ALL_A_LIQUID、PIT 历史成分股
- P1 实盘安全：订单幂等、重启恢复、交易计划人工确认、风控总开关、日报/报警
- P2 交叉验证：与聚宽/米筐/掘金/QMT 模拟盘对比每日净值、持仓、成交、费用和拒单

## 关键设计
- Strategy 只能通过 StrategyContext（只读快照）访问状态，不直接持有 Portfolio
- T+1：PositionSnapshot 区分 total_qty / tradeable_qty
- 无前视偏差：on_bar() 结束后提交信号，次日开盘撮合
- 配置：config/default.yaml + .env（Tushare token 等）
- 前端：Tailwind CSS（CDN）+ 原生 JS，无框架

---

# Web 调试规范

## 排查 Web 端页面问题

- **重点关注服务端接口交互**：排查 web 端页面问题时，优先检查 API 请求/响应、SSE 数据流、错误状态码等与后端的交互，而不是 UI 高亮、样式、CSS 类等视觉问题。
- **使用 Chrome DevTools MCP 调试**：不要用 Playwright 写测试的方式进行排查，直接使用 Chrome DevTools（https://skills.sh/chromedevtools/chrome-devtools-mcp/chrome-devtools）进行实时调试。

---

# 优化计划维护

- 项目优化路线以 `docs/claude_quant_optimization_plan.md` 为准。
- 每次开始较大的优化前，先查看该文件的“当前进度”和“下一步优化动作”。
- 每次完成优化、修复、调整优先级或发现新风险后，同步更新该文件进度，避免只依赖对话记忆。
- 提交代码前检查该计划文件是否需要同步更新。
