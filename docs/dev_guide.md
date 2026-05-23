# 开发说明

## 环境

- Python: `>=3.11,<3.13`
- 推荐使用虚拟环境，避免把依赖安装到系统 Python。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
```

如果自定义策略需要 `pandas-ta`：

```bash
python -m pip install -e ".[dev,indicators]"
```

## 测试

```bash
python -m pytest tests/unit
python -m pytest tests/integration
python -m ruff check cq web/routers scripts
```

- `tests/unit`：纯内存测试。
- `tests/integration`：以本地/内存数据验证完整流程，CI 已纳入。
- `ruff check cq web/routers scripts`：当前 lint 基线，新增核心代码应保持通过。
- Web 页面问题优先检查 API 响应、SSE 数据流和错误状态码。

## CI

GitHub Actions 会在 `main` 分支 push 和 pull request 时执行：

```bash
python -m pytest tests/unit tests/integration
```

当前测试矩阵为 Python `3.11` 和 `3.12`。如果后续支持 Python `3.13`，需要先确认核心依赖和本地数据流程都兼容，再调整 `pyproject.toml` 与 CI 矩阵。

`mypy --strict cq` 当前仍不是门禁；主要类型债务记录在 `docs/type_debt.md`。

## CLI

安装后可使用统一入口：

```bash
cq --help
cq factor-report --help
cq benchmark --help
cq cross-validate --help
cq cross-validation-template --help
cq import-pit-universe --help
cq validate-pit-universe --help
cq fetch-pit-universe --help
```

`scripts/*.py` 仍保留兼容旧用法，但新流程优先使用 `cq ...`。

## 优化计划

优化路线和当前进度记录在：

```text
docs/claude_quant_optimization_plan.md
```

每次完成较大优化、修复关键问题或调整优先级后，都要同步更新该文件。
