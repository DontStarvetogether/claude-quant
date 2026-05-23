# 平台交叉验证报告模板

> 用途：把 `claude-quant` 的标准 benchmark 输出和聚宽、米筐、掘金、QMT 模拟盘等外部平台输出逐项对账，确认差异来源。

## 1. 对照信息

| 项目 | 内容 |
|---|---|
| 本地策略 | 20日动量 TopN |
| 外部平台 | 待填写 |
| 股票池 | 待填写 |
| 复权方式 | 待填写 |
| 回测区间 | 待填写 |
| 初始资金 | 待填写 |
| 调仓频率 | 待填写 |
| 信号时间 | 收盘后生成 |
| 成交时间 | 下一交易日开盘 |
| 手续费 | 待填写 |
| 印花税 | 待填写 |
| 滑点 | 待填写 |

## 2. 输入文件

本地 benchmark 标准导出：

```text
equity_curve.csv
holdings.csv
trades.csv
signals.csv
summary.json
```

外部平台导出建议字段：

```text
equity_curve: date,total_assets,cash,position_value
holdings: date,symbol,quantity,market_value
trades: trade_date,symbol,side,quantity,price,amount,commission,stamp_tax,net_amount
```

## 3. 自动对账输出

使用 `cq.benchmark.compare_benchmark_with_external()` 后应保存：

```text
cross_validation_summary.json
cross_validation_report.md
equity_comparison.csv
holdings_comparison.csv
trades_comparison.csv
```

其中 `status` 字段含义：

| 状态 | 含义 |
|---|---|
| `matched` | 在容差内一致 |
| `different` | 本地和外部平台都有记录，但数值超过容差 |
| `missing_external` | 本地有记录，外部平台没有 |
| `missing_local` | 外部平台有记录，本地没有 |

## 4. 差异排查顺序

1. 复权方式是否一致。
2. 股票池是否 point-in-time，是否有幸存者偏差。
3. 调仓日、信号日、成交日是否一致。
4. 成交价格、手续费、印花税、滑点是否一致。
5. 涨跌停、停牌、新股、退市、ST 处理是否一致。
6. 现金使用、最小交易单位、最大单票仓位是否一致。
7. 持仓市值是否使用同一交易日收盘价。

## 5. 验收标准

进入下一阶段前，至少完成一次外部平台对账：

```text
每日净值差异可解释
每日成交差异可解释
每日持仓差异可解释
费用差异可解释
涨跌停 / 停牌 / T+1 行为差异可解释
```
