# 类型债务清单

> 最近检查：2026-05-23
> 命令：`python3 -m mypy cq --no-error-summary`
> 当前状态：未作为 CI 门禁，命令仍失败。

## 主要问题类别

1. Pandas / NumPy 类型推断噪声
   - 集中在 `cq/research/*`、`cq/performance/metrics.py`、`cq/universe/*`。
   - 典型问题是 DataFrame 标量被推断成大联合类型，导致 `int()`、日期运算、Series/DataFrame 选择被误报。

2. EventBus handler 泛型不精确
   - `EventBus.subscribe()` 目前使用宽泛 `AnyEvent` handler 类型。
   - `BacktestEngine` / `LiveEngine` 中按事件类型订阅具体 handler 时，mypy 认为参数类型不兼容。

3. StrategyContext 可空性
   - 示例策略直接使用 `self.ctx`，mypy 仍认为可能为 `None`。
   - 需要在策略基类中提供非空 accessor，或在 `_setup()` 后改变类型约束。

4. 外部数据源和 QMT 动态依赖
   - `akshare`、`baostock`、`xtquant` 的动态返回结构难以严格标注。
   - 当前主要依赖运行时校验和单测，后续可用 TypedDict / Protocol 收敛边界。

5. 配置 / 字典返回类型过宽
   - 若干 `dict` 未补泛型参数，部分 `Any` 返回值需要收敛。

## 建议顺序

1. 先修 `EventBus.subscribe()` 类型，让 engine/live 的订阅错误消失。
2. 给 `Strategy.ctx` 增加非空属性访问器，修示例策略。
3. 给 research/performance 的 DataFrame 边界加小型转换函数，避免 pandas 联合类型扩散。
4. 最后再处理外部数据源和 QMT adapter 的动态类型。
