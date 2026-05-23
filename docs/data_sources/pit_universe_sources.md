# PIT 股票池数据源说明

本项目的 point-in-time 股票池标准契约是：

```csv
universe_id,symbol,start_date,end_date,name
HS300_PIT,600519.SH,2020-01-01,,沪深300
```

权重快照单独保存为：

```csv
universe_id,symbol,trade_date,weight
hs300_pit,600519.SH,2020-01-02,5.23
```

## 免费入口：AkShare / 中证指数公开快照

项目现在支持用 AkShare 读取中证指数公开的最新成分和权重快照：

- 适用场景：免费搭建当前股票池、快速跑 benchmark、和付费/平台数据做横向校验。
- 当前限制：AkShare 的中证接口主要返回最新公开快照，不是完整历史调入调出链路；因此输出会标记为 `free_best_effort_latest_snapshot`，不能直接当作严格历史 PIT 使用。
- 字段来源：`日期,指数代码,成分券代码,成分券名称,交易所,权重`。
- 项目默认映射：
  - `HS300_PIT = 000300`
  - `ZZ500_PIT = 000905`
  - `ZZ1000_PIT = 000852`
- 使用方式：

```bash
cq fetch-pit-universe \
  --provider akshare \
  --start 2024-01-01 \
  --end 2026-05-23 \
  --output data/universes/pit_memberships.csv \
  --weights-output data/universes/pit_weights.csv \
  --raw-dir data/raw/akshare/csindex \
  --validation-dir output/universe_validation
```

AkShare provider 优先调用 `index_stock_cons_weight_csindex`，如果权重接口不可用，会回退到 `index_stock_cons_csindex` 并保留成分股，`pit_weights.csv` 中权重为空。原始文件保存到 `data/raw/akshare/csindex/{endpoint}/{index_code}/{YYYYMMDD}.csv`。

下载完成后，`--validation-dir` 目录会同时写出：

- `pit_fetch_summary.json`：记录 provider、数据质量、快照日期、`effective_coverage_start`、输出文件路径。
- `pit_fetch_report.md`：记录面向人工复核的数据源质量、严格历史 PIT 标记、快照日期、接口回退和输出文件路径。
- `pit_validation_summary.json`：记录 PIT 契约校验结果。
- `pit_validation_issues.csv`：记录可机器处理的校验问题。
- `pit_validation_report.md`：记录面向人工复核的校验报告。

同时会在 PIT CSV 旁写出同名 sidecar，例如 `data/universes/pit_memberships.summary.json`。该 sidecar 会记录 `pit_fetch_report.md` 路径；`cq benchmark --pit-csv data/universes/pit_memberships.csv` 会自动读取该 sidecar，并把 `universe_source` 和 `universe_quality_warning` 写入 benchmark 的 `summary.json`。

## 严格历史主线：Tushare Pro

严格历史自动下载使用 Tushare Pro 的 `index_weight` 接口。

- 适用指数：沪深300、中证500、中证1000等主流指数。
- 字段来源：`index_code,con_code,trade_date,weight`。
- 项目默认映射：
  - `HS300_PIT = 399300.SZ`
  - `ZZ500_PIT = 000905.SH`
  - `ZZ1000_PIT = 000852.SH`
- 权限要求：需要 `TUSHARE_TOKEN`，并且账号具备 `index_weight` 调用权限。
- 使用方式：

```bash
cq fetch-pit-universe \
  --provider tushare \
  --start 2015-01-01 \
  --end 2026-05-23 \
  --output data/universes/pit_memberships.csv \
  --weights-output data/universes/pit_weights.csv \
  --raw-dir data/raw/tushare/index_weight \
  --validation-dir output/universe_validation
```

下载过程会按月请求 Tushare，并保存原始月度文件到 `data/raw/tushare/index_weight/{index_code}/{YYYYMM}.csv`。该目录是本地数据产物，不应提交到仓库。

## 备选和交叉校验来源

| 来源 | 适用性 | 说明 |
|---|---|---|
| AkShare | 免费入口 / 当前快照 | 免费、方便，已接入 `cq fetch-pit-universe --provider akshare`；适合当前成分和权重快照，不能替代严格历史 PIT。 |
| Tushare Pro | 严格历史主线 | 已接入 `cq fetch-pit-universe --provider tushare`；适合构建历史 PIT，但需要 token 和接口权限。 |
| JoinQuant | 交叉校验 | `get_index_stocks(index_symbol, date=None)` 可按日期导出指数成分，适合抽样验证 Tushare 结果。 |
| RiceQuant / RQData | 交叉校验 | `index_components(order_book_id, date=...)` 支持历史构成查询，适合平台对账。 |
| Wind / Choice / iFinD | 商业数据源 | 数据质量通常更稳，但需要本地授权环境，暂不作为项目默认自动入口。 |

## 校验要求

下载或导入后必须运行 PIT 校验：

```bash
cq validate-pit-universe \
  --input data/universes/pit_memberships.csv \
  --expected-universe HS300_PIT \
  --expected-universe ZZ500_PIT \
  --expected-universe ZZ1000_PIT \
  --coverage-start 2015-01-01 \
  --coverage-end 2026-05-23 \
  --output-dir output/universe_validation
```

校验重点：

- 预期股票池是否存在。
- 股票代码是否为 `000001.SZ` / `600000.SH` / `430047.BJ` 格式。
- 同一股票在同一股票池内是否存在重叠生效区间。
- 起止日期是否能解析出有效成分股。
- 成分股数量是否低于合理阈值。
