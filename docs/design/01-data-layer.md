# 数据层设计

## 职责边界

| 组件 | 职责 | 不负责 |
|------|------|--------|
| `DataSource` | 从外部 API 拉取原始数据，标准化列名和类型 | 缓存、存储、复权计算 |
| `DataStore` | Parquet 文件的读写，分区管理，增量更新幂等性 | 网络请求、复权 |
| `PriceAdjuster` | 根据复权因子计算前复权/后复权价格 | 数据下载、存储 |
| `TradingCalendar` | 判断交易日、计算下一交易日、节假日 | 其他 |
| `DataPipeline` | 协调 DataSource + DataStore + PriceAdjuster 完成下载任务 | 策略、引擎 |
| `DataFeed` | 从 DataStore 按时间顺序推送 `BarEvent` 给引擎 | 下载、存储 |

---

## DataSource ABC

```python
class DataSource(ABC):

    @abstractmethod
    def fetch_daily_bars(
        self,
        symbol: str,          # "600519.SH" 格式
        start_date: date,
        end_date: date,
        adjust: str = "none", # DataSource 只返回原始价格，复权由 PriceAdjuster 处理
    ) -> pd.DataFrame:
        """
        返回 DataFrame，列名标准：
          trade_date: date
          open, high, low, close: float（元）
          volume: int（股）
          amount: float（元）
          pre_close: float（昨收，未复权）
          is_st: bool
          is_suspended: bool

        注意：涨跌停价由 DataPipeline 用 AStockRules 计算后补充，
        DataSource 不负责计算（避免各数据源计算规则不一致）。
        """

    @abstractmethod
    def fetch_adj_factors(
        self, symbol: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """
        返回：trade_date(date), adj_factor(float)
        adj_factor 定义：当日价格 * adj_factor = 前复权价格（相对今日）
        除权日后 adj_factor 会下调，历史数据需重新计算。
        """

    @abstractmethod
    def fetch_trading_calendar(self, exchange: str, year: int) -> list[date]:
        """
        exchange: "SSE"（上交所）| "SZSE"（深交所）
        返回指定年份的所有交易日列表，升序排列。
        """

    @abstractmethod
    def fetch_stock_info(self, symbol: str) -> dict:
        """
        返回：
          name: str           股票名称
          list_date: date     上市日期
          delist_date: date | None  退市日期
          exchange: str       "SH" | "SZ" | "BJ"
          industry: str       申万行业
          board: str          "main" | "star" | "gem" | "bj"（板块）
        """
```

### BaostockSource 实现要点

- 登录状态用单例管理，自动重连
- 股票代码转换：`600519.SH` → `sh.600519`（baostock 格式）
- `adj_factor` 字段来自 `bs.query_adjust_factor()`
- 停牌判断：`volume == 0 and amount == 0`
- **不使用 baostock 的内置复权**（`adjustflag="3"` 即不复权），由我们自己计算确保一致性

### TushareSource 实现要点

- Token 从 `os.getenv("TUSHARE_TOKEN")` 读取，启动时检查
- 使用 `daily()` 接口获取日线，`adj_factor()` 获取复权因子
- 速率限制处理：失败自动 retry（指数退避，最多 3 次）
- 全市场覆盖（vs baostock 只有中证500）

---

## DataStore ABC + ParquetStore 实现

### 目录结构

```
{data_root}/
  bars/
    {exchange}/           # SH / SZ / BJ
      {code}/             # 6位代码，不含后缀
        raw.parquet       # 原始未复权价格（永远追加不覆写）
        qfq.parquet       # 前复权价格（除权时重算）
        adj_factors.parquet
  calendar/
    SSE.parquet
    SZSE.parquet
    BSE.parquet
  stock_info/
    all.parquet           # 全市场股票基础信息
```

### Parquet Schema

**bars/raw.parquet 和 qfq.parquet**：

```
trade_date:   date32       ← pyarrow date32，不用 timestamp
open:         float32
high:         float32
low:          float32
close:        float32
volume:       int64
amount:       float64      ← 成交额用 float64，数值较大
pre_close:    float32      ← 仅 raw.parquet 有，qfq.parquet 无需
adj_factor:   float32      ← qfq.parquet 额外有此列
is_st:        bool
is_suspended: bool
limit_up:     float32
limit_down:   float32
```

**metadata**：

```python
schema.with_metadata({
    "cq_schema_version": "1",
    "symbol": "600519.SH",
    "adjust_type": "qfq",   # 或 "raw"
    "source": "baostock",
    "last_updated": "2024-01-01T00:00:00",
})
```

### 幂等写入

```python
def write_daily_bars(self, symbol: str, df: pd.DataFrame, mode="append"):
    path = self._bar_path(symbol, "raw")
    if mode == "append" and path.exists():
        existing = pd.read_parquet(path)
        # 合并 + 去重（trade_date 为唯一键，新数据优先）
        combined = (
            pd.concat([existing, df])
            .sort_values("trade_date")
            .drop_duplicates(subset=["trade_date"], keep="last")
        )
    else:
        combined = df
    combined.to_parquet(path, engine="pyarrow", index=False)
```

### 高效批量读取

使用 pyarrow predicate pushdown，只读取需要的日期范围：

```python
def read_daily_bars(self, symbols, start_date, end_date) -> pd.DataFrame:
    filters = [
        ("trade_date", ">=", start_date),
        ("trade_date", "<=", end_date),
    ]
    dfs = []
    for sym in symbols:
        path = self._bar_path(sym, "qfq")
        if not path.exists():
            logger.warning(f"本地无数据: {sym}，请先运行 download_data.py")
            continue
        df = pd.read_parquet(path, filters=filters)
        df.insert(0, "symbol", sym)
        dfs.append(df)

    if not dfs:
        return pd.DataFrame()

    return (
        pd.concat(dfs, ignore_index=True)
        .sort_values(["trade_date", "symbol"])
        .reset_index(drop=True)
    )
```

---

## PriceAdjuster

### 前复权原理

以**今日**为基准，向历史方向调整。保证今日价格不变，历史价格等比缩放：

```
前复权价格(t) = 原始价格(t) × (今日adj_factor / t日adj_factor)
```

除权日后 `adj_factor` 下调（例如：10送10 后 adj_factor 从 1.0 变为约 0.5），
历史价格序列在除权日会有一个"跳跃"，前复权消除这个跳跃。

```python
class PriceAdjuster:

    def apply_qfq(
        self,
        raw_df: pd.DataFrame,      # raw.parquet 数据
        adj_df: pd.DataFrame,      # adj_factors.parquet 数据
    ) -> pd.DataFrame:
        """
        返回前复权 DataFrame。
        价格列（open/high/low/close）乘以复权因子。
        volume 不调整（股数不变）。
        pre_close 列移除（前复权后无意义）。
        """
        merged = raw_df.merge(adj_df, on="trade_date", how="left")
        latest_factor = adj_df["adj_factor"].iloc[-1]

        price_cols = ["open", "high", "low", "close"]
        for col in price_cols:
            merged[col] = (
                merged[col] * latest_factor / merged["adj_factor"]
            ).round(3)  # 保留3位小数

        merged["adj_factor"] = latest_factor / merged["adj_factor"]
        return merged.drop(columns=["pre_close"])

    def detect_split_dates(self, adj_df: pd.DataFrame) -> list[date]:
        """返回复权因子发生变化的日期（即除权日）"""
        changed = adj_df["adj_factor"].diff().abs() > 1e-6
        return adj_df.loc[changed, "trade_date"].tolist()
```

---

## TradingCalendar

```python
class TradingCalendar:
    """
    交易日历。数据来源：DataSource.fetch_trading_calendar()，本地缓存在 Parquet。

    使用 frozenset 存储交易日，O(1) 判断。
    """

    def is_trading_day(self, d: date) -> bool: ...

    def next_trading_day(self, d: date, n: int = 1) -> date:
        """返回 d 之后第 n 个交易日"""
        ...

    def prev_trading_day(self, d: date, n: int = 1) -> date: ...

    def trading_days_between(self, start: date, end: date) -> list[date]:
        """返回 [start, end] 之间的所有交易日，包含两端"""
        ...

    def count_trading_days(self, start: date, end: date) -> int: ...
```

---

## DataFeed

```python
class HistoricalFeed:
    """
    从 ParquetStore 读取数据，按交易日顺序推送 BarEvent。

    内存策略：全量加载到 DataFrame（回测期间一般不超过 500MB），
    按 trade_date groupby 迭代，避免逐行读 Parquet 的开销。
    """

    def iter_by_date(self) -> Iterator[tuple[date, list[Bar]]]:
        """
        每次迭代返回一个交易日的所有 Bar。
        同一日期内，Bar 的顺序与 symbols 列表顺序一致。
        """
        ...

    def get_history(
        self, symbol: str, current_date: date, n: int
    ) -> pd.DataFrame:
        """
        返回 symbol 在 current_date（含）之前的 n 根 bar。
        供 StrategyContext.get_bar_history() 调用。
        使用预构建的多级索引，O(log n) 查询。
        """
        ...
```

---

## DataPipeline（协调器）

```python
class DataPipeline:
    """
    协调 DataSource + PriceAdjuster + DataStore，完成：
    1. 增量下载（只下载本地没有的日期范围）
    2. 复权因子更新（除权后重算 qfq.parquet）
    3. 交易日历同步
    4. 股票基础信息更新
    """

    def update_symbol(
        self,
        symbol: str,
        end_date: date = None,   # 默认今日
        force: bool = False,      # True: 忽略本地数据，强制全量下载
    ) -> int:
        """
        增量更新单只股票。
        返回新增的 bar 数量。
        """
        local_min, local_max = self._store.get_available_dates(symbol)

        if local_max is None or force:
            # 全量下载
            start = self._get_list_date(symbol)
        else:
            # 增量：从本地最新日期的次日开始
            start = self._calendar.next_trading_day(local_max)

        if start > end_date:
            logger.info(f"{symbol} 数据已是最新")
            return 0

        raw_df = self._source.fetch_daily_bars(symbol, start, end_date)
        adj_df = self._source.fetch_adj_factors(symbol, start, end_date)

        # 补全涨跌停价
        raw_df = self._fill_limit_prices(raw_df)

        # 写入原始数据
        self._store.write_daily_bars(symbol, raw_df, mode="append")
        self._store.write_adj_factors(symbol, adj_df, mode="append")

        # 重算前复权（除权影响整个历史序列）
        if self._adjuster.detect_split_dates(adj_df):
            self._recalculate_qfq(symbol)

        return len(raw_df)

    def update_batch(
        self,
        symbols: list[str],
        max_workers: int = 10,
    ) -> dict[str, int]:
        """并行更新多只股票，返回 {symbol: 新增bar数}"""
        ...
```
