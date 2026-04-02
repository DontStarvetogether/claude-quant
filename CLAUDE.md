# 项目概览

## 目标
面向初学者的 A 股量化交易框架，支持策略回测和实盘交易。Python + FastAPI + 纯 HTML/JS 前端，端口 8888。

## 目录结构

```
cq/
  core/       models.py(Bar/Order/Trade/Position/Account/Signal), events.py, event_bus.py
  data/       source/(baostock,akshare), store/parquet_store.py, feed/historical.py, adjust/adjuster.py, calendar.py
  engine/     backtest_engine.py, portfolio.py, matching/bar_matching.py
  strategy/   base.py(Strategy ABC + StrategyContext), examples/double_ma.py
  execution/  simulated.py（只有模拟，无实盘）
  risk/       pre_trade.py
  performance/metrics.py

web/
  app.py          FastAPI 主入口
  routers/        backtest.py, strategy.py, symbols.py
  schemas.py      Pydantic 请求/响应模型
  serializers.py  结果序列化
  runner.py       回测任务运行器
  static/
    index.html    回测配置页（主页）
    result.html   回测结果页
    js/           main.js, api.js, result.js

scripts/    run_backtest.py, download_data.py, download_all_stocks.py
run_web.py  启动入口
```

## 已实现模块
- 事件驱动引擎：优先级队列（MARKET_DATA→SIGNAL→ORDER→FILL→EOD）
- 数据层：Baostock/AKShare，Parquet 存储，前复权，交易日历
- 回测引擎：D日信号→D+1开盘成交（无前视偏差），T+1结算，涨跌停拒单
- 风控：单股仓位上限、最低现金比例、T+1 校验
- 绩效：总收益/年化/最大回撤/夏普/索提诺/卡玛/胜率/盈亏比
- Web：回测配置UI + 结果页，SSE 实时进度，/api/backtest|strategy|symbols

## 缺失模块（待实现）
- P0 实盘：`cq/execution/qmt.py`（QMT执行器）、`cq/data/feed/realtime.py`、`cq/live/engine.py`
- P1 Web页面：`live.html`（实盘监控）、`data.html`（数据管理）、`strategies.html`（策略库）、`compare.html`（策略对比）
- P2 初学者：更多策略示例（目前只有 DoubleMaStrategy），result.html 缺净值曲线/回撤图/月度热力图

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
