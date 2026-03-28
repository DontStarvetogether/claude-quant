# 风控设计

## 职责划分

| 组件 | 触发时机 | 职责 |
|------|----------|------|
| `PreTradeRisk` | SignalEvent → OrderEvent 前 | 单笔下单校验：仓位上限、资金够不够、T+1 限制、涨跌停不追高 |
| `PortfolioRisk` | EndOfDayEvent 后 | 组合级别检查：总回撤止损、持仓集中度 |

两者均不修改订单，只做通过/拒绝决策。拒绝时发出 `RejectEvent`，策略通过 `on_order_update` 感知。

---

## PreTradeRisk

### 检查顺序

```
SignalEvent 到达
  │
  ├─ 1. 股票是否停牌？             → 停牌拒绝
  ├─ 2. 买入时：是否涨停开盘？      → 涨停拒绝（实际上在 BarMatchingEngine 也检查，这里是前置过滤）
  ├─ 3. 资金/数量是否合法？         → 金额为零（量 < 100 股）拒绝
  ├─ 4. 买入：单股仓位是否超上限？   → 超过 max_position_pct 拒绝
  ├─ 5. 买入：现金储备是否足够？     → 买入后现金 < min_cash_reserve * total_assets 拒绝
  └─ 6. 卖出：T+1 可卖数量检查      → tradeable_qty < 请求卖出量 拒绝
```

### 实现

```python
class PreTradeRisk:

    def __init__(self, portfolio: PortfolioManager, config: RiskConfig):
        self._portfolio = portfolio
        self._config = config

    def check(self, event: SignalEvent) -> tuple[bool, str]:
        """
        返回 (passed, reason)。
        reason 在 passed=False 时描述拒绝原因。
        """
        sig = event.signal

        if sig.side == OrderSide.BUY:
            return self._check_buy(sig)
        else:
            return self._check_sell(sig)

    def _check_buy(self, sig: Signal) -> tuple[bool, str]:
        total_assets = self._portfolio.get_total_assets()
        cash = self._portfolio.get_cash()
        pos = self._portfolio.get_position(sig.symbol)

        # 计算本次买入金额
        buy_amount = self._calc_buy_amount(sig, total_assets)
        if buy_amount <= 0:
            return False, "买入金额为零（量 < 100 股）"

        # 单股仓位上限
        current_pos_value = (pos.total_qty * pos.last_price) if pos else 0.0
        new_pos_pct = (current_pos_value + buy_amount) / total_assets
        if new_pos_pct > self._config.max_position_pct:
            return False, (
                f"单股仓位 {new_pos_pct:.1%} 超过上限 {self._config.max_position_pct:.1%}"
            )

        # 现金储备检查
        remaining_cash = cash - buy_amount
        min_reserve = total_assets * self._config.min_cash_reserve
        if remaining_cash < min_reserve:
            return False, (
                f"买入后现金 {remaining_cash:.0f} 低于最低储备 {min_reserve:.0f}"
            )

        return True, ""

    def _check_sell(self, sig: Signal) -> tuple[bool, str]:
        pos = self._portfolio.get_position(sig.symbol)
        if pos is None:
            return False, f"无持仓: {sig.symbol}"

        # 计算卖出股数
        sell_qty = self._calc_sell_qty(sig, pos)
        if sell_qty <= 0:
            return False, "卖出数量为零"

        if pos.tradeable_qty < sell_qty:
            return False, (
                f"T+1限制: 请求卖出 {sell_qty} 股，可卖 {pos.tradeable_qty} 股"
            )

        return True, ""

    def _calc_buy_amount(self, sig: Signal, total_assets: float) -> float:
        """将 Signal 的 percent/amount/quantity 统一转为买入金额"""
        if sig.quantity is not None:
            # 指定股数：quantity 已是 100 整数倍（由 Strategy.buy() 保证）
            price = sig.limit_price or self._portfolio.get_last_price(sig.symbol)
            return price * sig.quantity if price else 0.0
        elif sig.percent is not None:
            return total_assets * sig.percent
        elif sig.amount is not None:
            return sig.amount
        return 0.0

    def _calc_sell_qty(self, sig: Signal, pos: Position) -> int:
        if sig.quantity is not None:
            return sig.quantity
        elif sig.percent is not None:
            return int(pos.tradeable_qty * sig.percent)
        return pos.tradeable_qty  # 默认全卖
```

---

## PortfolioRisk

组合级别的风控在每日收盘后触发，可以强制平仓或暂停策略发单。

```python
class PortfolioRisk:

    def __init__(self, portfolio: PortfolioManager, config: RiskConfig):
        self._portfolio = portfolio
        self._config = config
        self._peak_assets: float = 0.0
        self._strategy_halted: bool = False

    def check_eod(self, trade_date: date) -> list[str]:
        """
        每日 EOD 调用，返回触发的风控规则列表（空表示无风险）。
        副作用：如触发最大回撤止损，设置 _strategy_halted = True。
        """
        warnings = []
        total = self._portfolio.get_total_assets()

        # 更新峰值
        self._peak_assets = max(self._peak_assets, total)

        # 最大回撤止损
        if self._peak_assets > 0:
            drawdown = (self._peak_assets - total) / self._peak_assets
            if drawdown >= self._config.max_drawdown_stop:
                self._strategy_halted = True
                warnings.append(
                    f"最大回撤 {drawdown:.1%} 触发止损（阈值 {self._config.max_drawdown_stop:.1%}）"
                )

        # 持仓集中度检查（仅警告，不强制平仓）
        positions = self._portfolio.get_all_positions()
        for sym, pos in positions.items():
            pos_pct = pos.market_value / total if total > 0 else 0
            if pos_pct > self._config.max_position_pct * 1.2:  # 超过上限 20% 才警告
                warnings.append(
                    f"{sym} 持仓占比 {pos_pct:.1%} 偏高"
                )

        return warnings

    @property
    def is_halted(self) -> bool:
        """策略是否已被组合风控暂停"""
        return self._strategy_halted
```

---

## RiskConfig

```python
@dataclass
class RiskConfig:
    # 单股最大仓位（占总资产）
    max_position_pct: float = 0.20

    # 最大回撤止损阈值（触发后暂停所有新信号）
    max_drawdown_stop: float = 0.15

    # 最低现金储备（占总资产）
    min_cash_reserve: float = 0.05

    # 单日最大交易笔数（防止过度交易）
    max_daily_trades: int = 50
```

---

## 风控与引擎的集成

```
SignalEvent
    │
    ▼
PreTradeRisk.check()
    ├─ passed=True  → Executor.on_signal() → OrderEvent
    └─ passed=False → RejectEvent（reason 传给 strategy.on_order_update）

EndOfDayEvent
    │
    ▼
PortfolioRisk.check_eod()
    ├─ 无风险       → 继续
    └─ 触发回撤止损  → is_halted=True，后续 SignalEvent 全部直接拒绝
```

### 被暂停后的行为

```python
# SimulatedExecutor.on_signal() 检查暂停状态
def on_signal(self, event: SignalEvent) -> None:
    if self._portfolio_risk.is_halted:
        self._bus.put(RejectEvent(
            order_id=event.signal.signal_id,
            reason="组合风控已暂停：最大回撤止损",
        ))
        return
    # 正常流程
    passed, reason = self._pre_trade_risk.check(event)
    ...
```

---

## 注意事项

1. **回测中不做实时止损**：`PortfolioRisk` 的暂停状态在整个回测中保持，不会在次日自动恢复。这与实盘行为一致（人工干预才能恢复）。

2. **PreTradeRisk 不持有 bar 数据**：涨跌停判断在 `BarMatchingEngine` 中（D+1 撮合时）做，PreTradeRisk 只做可量化的账户检查。

3. **仓位上限是软约束**：市值波动可能导致持仓比例超过 `max_position_pct`，但风控只在下单时检查，不会因价格变动自动平仓。
