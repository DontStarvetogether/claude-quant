# 回测引擎设计

## 核心原则

**回测的价值在于结果可信。** 任何一处不正确的建模，都会导致策略表现虚高，实盘亏损。

本引擎对以下三个问题给出明确答案：

| 问题 | 本引擎的回答 |
|------|-------------|
| 何时成交？ | D 日信号，D+1 日开盘价成交（无前视偏差） |
| 当日买入能否当日卖出？ | 不能（T+1 双重保证） |
| 涨跌停时如何处理？ | 开盘即封板时拒绝，正常开盘按开盘价成交 |

---

## BacktestEngine 主循环

```
初始化
  │
  ├─ 创建 EventBus
  ├─ 创建 PortfolioManager(initial_capital)
  ├─ 创建 BarMatchingEngine
  ├─ 创建 PreTradeRisk
  ├─ 创建 SimulatedExecutor
  └─ 注册事件订阅

按交易日迭代
  │
  for trade_date, bars in feed.iter_by_date():
  │
  ├─ 1. BarMatchingEngine.process_pending_orders(trade_date)
  │      └─ 对前一日挂单用今日 bar 撮合（D+1 成交）
  │
  ├─ 2. strategy.before_trading(trade_date)
  │
  ├─ 3. for bar in bars:
  │      └─ bus.put(BarEvent(bar))
  │
  ├─ 4. bus.dispatch_all()
  │      顺序：MARKET_DATA → SIGNAL → ORDER → FILL
  │      │
  │      ├─ BarEvent → strategy.on_bar() → SignalEvent
  │      ├─ SignalEvent → risk.check() → OrderEvent / RejectEvent
  │      ├─ OrderEvent → matching.queue_order()（不立即成交！）
  │      └─ FillEvent → portfolio.on_fill() + strategy.on_order_update()
  │
  ├─ 5. bus.put(EndOfDayEvent(trade_date))
  │     bus.dispatch_all()
  │     └─ portfolio.settle_eod()（解锁 T+1，today_bought_qty → tradeable_qty）
  │
  └─ 6. strategy.after_trading(trade_date)
       perf_tracker.record(trade_date, portfolio.snapshot())

计算并返回 BacktestResult
```

---

## BarMatchingEngine

### 关键设计：订单队列按日期隔离

```python
class BarMatchingEngine:

    def __init__(self, bus: EventBus, config: Config):
        self._bus = bus
        self._config = config
        # 待处理订单：{下单交易日: [Order]}
        # D 日的订单在 D+1 才会被处理
        self._pending: dict[date, list[Order]] = defaultdict(list)
        # D+1 的 bar 缓存，供撮合时查询
        self._current_bars: dict[str, Bar] = {}

    def on_bar(self, event: BarEvent) -> None:
        """每日 bar 到达时，更新当日价格缓存"""
        self._current_bars[event.bar.symbol] = event.bar

    def on_order(self, event: OrderEvent) -> None:
        """将订单放入前一交易日的 pending 队列"""
        order = event.order
        # 订单在 D 日创建，D+1 撮合
        self._pending[order.trade_date].append(order)

    def process_pending_orders(self, today: date) -> None:
        """
        在 today 的 bar 推送前调用。
        处理 yesterday 的所有挂单，用 today 的 bar 撮合。
        """
        yesterday = self._calendar.prev_trading_day(today)
        pending = self._pending.pop(yesterday, [])

        for order in pending:
            bar = self._current_bars.get(order.symbol)
            if bar is None:
                self._reject(order, f"无行情数据: {order.symbol}")
                continue
            self._match(order, bar)

    def _match(self, order: Order, bar: Bar) -> None:
        """
        用 today 的 bar 撮合 yesterday 的订单。
        bar 就是今日（D+1）的 bar。
        """
        # 停牌
        if bar.is_suspended:
            self._reject(order, "停牌")
            return

        if order.side == OrderSide.BUY:
            self._match_buy(order, bar)
        else:
            self._match_sell(order, bar)

    def _match_buy(self, order: Order, bar: Bar) -> None:
        fill_price = bar.open

        # 涨停开盘：无法买入
        if fill_price >= bar.limit_up:
            self._reject(order, f"涨停开盘({fill_price:.2f})，无法买入")
            return

        # 限价单：委托价低于开盘价，无法成交
        if order.order_type == OrderType.LIMIT:
            if order.limit_price < fill_price:
                self._reject(order, f"限价{order.limit_price:.2f} < 开盘{fill_price:.2f}")
                return
            fill_price = min(order.limit_price, fill_price)  # 以较低价成交

        self._fill(order, fill_price, bar)

    def _match_sell(self, order: Order, bar: Bar) -> None:
        # T+1 最终检查（PortfolioManager 已经检查过，这里是最后防线）
        pos = self._portfolio.get_position(order.symbol)
        if pos is None or pos.tradeable_qty < order.quantity:
            tradeable = pos.tradeable_qty if pos else 0
            self._reject(order, f"T+1限制: 可卖{tradeable}股")
            return

        fill_price = bar.open

        # 跌停封板：无法卖出（简化模型：开盘即跌停且全天封板）
        if fill_price <= bar.limit_down and bar.close <= bar.limit_down:
            self._reject(order, f"跌停封板({fill_price:.2f})，无法卖出")
            return

        if order.order_type == OrderType.LIMIT:
            if order.limit_price > fill_price:
                self._reject(order, f"限价{order.limit_price:.2f} > 开盘{fill_price:.2f}")
                return

        self._fill(order, fill_price, bar)

    def _fill(self, order: Order, price: float, bar: Bar) -> None:
        amount = price * order.quantity
        commission = max(amount * self._config.commission_rate, self._config.min_commission)
        stamp_tax = amount * self._config.stamp_tax_rate if order.side == OrderSide.SELL else 0.0

        trade = Trade(
            trade_id=self._gen_trade_id(),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=price,
            amount=amount,
            commission=commission,
            stamp_tax=stamp_tax,
            trade_time=datetime.combine(bar.trade_date, time(9, 30)),
            trade_date=bar.trade_date,
        )
        self._bus.put(FillEvent(timestamp=trade.trade_time, trade=trade))

    def _reject(self, order: Order, reason: str) -> None:
        self._bus.put(RejectEvent(
            timestamp=datetime.now(),
            order_id=order.order_id,
            reason=reason,
        ))
```

---

## PortfolioManager

### 持仓更新逻辑

```python
class PortfolioManager:

    def on_fill(self, event: FillEvent) -> None:
        trade = event.trade

        if trade.side == OrderSide.BUY:
            pos = self._get_or_create_position(trade.symbol)

            # 更新均价（加权平均，含佣金摊入）
            total_cost = pos.avg_cost * pos.total_qty + trade.amount + trade.commission
            new_total_qty = pos.total_qty + trade.quantity
            pos.avg_cost = total_cost / new_total_qty

            pos.total_qty = new_total_qty
            pos.today_bought_qty += trade.quantity
            # tradeable_qty 不增加！等 EOD settle 后才解锁

            self.account.cash -= (trade.amount + trade.commission)

        else:  # SELL
            pos = self.account.positions[trade.symbol]
            pos.total_qty -= trade.quantity
            pos.tradeable_qty -= trade.quantity

            # 卖出收入扣除手续费和印花税
            net_proceeds = trade.amount - trade.commission - trade.stamp_tax
            self.account.cash += net_proceeds

            # 持仓清零时删除
            if pos.total_qty == 0:
                del self.account.positions[trade.symbol]

    def settle_eod(self, event: EndOfDayEvent) -> None:
        """
        每日收盘后调用，执行 T+1 解锁。
        这是 T+1 约束的"解锁"端，on_fill 的 buy 分支是"锁定"端。
        """
        for pos in self.account.positions.values():
            pos.tradeable_qty += pos.today_bought_qty
            pos.today_bought_qty = 0

    def update_prices(self, bars: list[Bar]) -> None:
        """用当日收盘价更新持仓市值（用于权益曲线记录）"""
        for bar in bars:
            if bar.symbol in self.account.positions:
                self.account.positions[bar.symbol].last_price = bar.close

    def snapshot(self) -> AccountSnapshot:
        """
        返回当日快照（frozen dataclass），供 PerformanceTracker 记录。
        快照包含：cash, positions 副本, total_assets, net_assets
        """
        ...
```

---

## PerformanceMetrics

所有指标在回测结束后一次性计算，不在主循环中计算（避免分散逻辑）。

### 权益曲线

`PerformanceTracker.record(date, snapshot)` 每日记录一个快照，
回测结束后转为 DataFrame：`equity_curve[date] = net_assets`。

**关键**：每日只记录一次（在 EOD settle 之后），不会出现现有系统的重复记录问题。

### 指标计算

```python
class PerformanceMetrics:

    def compute(self, equity_curve: pd.Series, trades: list[Trade]) -> dict:
        """
        equity_curve: index=date, values=净资产
        返回完整指标字典
        """
        initial = equity_curve.iloc[0]
        final = equity_curve.iloc[-1]

        # 1. 总收益率
        total_return = (final - initial) / initial

        # 2. 年化收益率（使用实际交易日天数，不用自然日）
        n_days = len(equity_curve) - 1   # 交易日数
        annual_return = (1 + total_return) ** (252 / n_days) - 1 if n_days > 0 else 0

        # 3. 最大回撤（精确计算 underwater curve）
        rolling_max = equity_curve.cummax()
        drawdown = (equity_curve - rolling_max) / rolling_max
        max_drawdown = drawdown.min()   # 负数

        # 4. 夏普比率
        daily_returns = equity_curve.pct_change().dropna()
        rf_daily = 0.03 / 252    # 无风险利率年化 3%
        excess = daily_returns - rf_daily
        sharpe = (excess.mean() / excess.std() * np.sqrt(252)
                  if excess.std() > 0 else 0)

        # 5. 卡玛比率
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0

        # 6. 交易统计（配对买卖）
        win_rate, avg_profit, avg_loss, profit_factor = self._calc_trade_stats(trades)

        return {
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "max_drawdown": round(max_drawdown, 6),
            "sharpe_ratio": round(sharpe, 4),
            "calmar_ratio": round(calmar, 4),
            "volatility": round(daily_returns.std() * np.sqrt(252), 6),
            "win_rate": round(win_rate, 4),
            "avg_profit": round(avg_profit, 6),
            "avg_loss": round(avg_loss, 6),
            "profit_factor": round(profit_factor, 4),
            "total_trades": len([t for t in trades if t.side == OrderSide.BUY]),
            "final_value": round(final, 2),
            "total_fees": round(sum(t.commission + t.stamp_tax for t in trades), 2),
        }

    def _calc_trade_stats(self, trades):
        """
        配对买卖计算盈亏：
        每个 BUY 与之后对应 SELL 配对（FIFO）
        """
        profits = []
        buy_queue: dict[str, list[Trade]] = defaultdict(list)

        for trade in sorted(trades, key=lambda t: t.trade_time):
            if trade.side == OrderSide.BUY:
                buy_queue[trade.symbol].append(trade)
            elif buy_queue[trade.symbol]:
                buy_trade = buy_queue[trade.symbol].pop(0)
                pnl_pct = (trade.price - buy_trade.price) / buy_trade.price
                profits.append(pnl_pct)

        if not profits:
            return 0, 0, 0, 0

        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        win_rate = len(wins) / len(profits)
        avg_profit = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        profit_factor = (abs(sum(wins)) / abs(sum(losses))
                         if losses and sum(losses) != 0 else float("inf"))

        return win_rate, avg_profit, avg_loss, profit_factor
```

---

## BacktestResult

```python
@dataclass
class BacktestResult:
    # 基础信息
    strategy_name: str
    symbols: list[str]
    start_date: date
    end_date: date
    initial_capital: float

    # 绩效指标
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    calmar_ratio: float
    volatility: float
    win_rate: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    total_trades: int
    final_value: float
    total_fees: float

    # 详细数据
    equity_curve: pd.Series        # index=date, values=net_assets
    trades: list[Trade]            # 完整成交记录
    rejected_orders: list[tuple]   # [(order_id, reason), ...]
    daily_snapshots: pd.DataFrame  # 每日持仓明细快照

    def summary(self) -> str:
        """打印摘要"""
        ...

    def to_dict(self) -> dict:
        """序列化为 JSON 兼容字典"""
        ...
```
