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
```

- `tests/unit`：纯内存测试，CI 默认执行。
- `tests/integration`：依赖本地行情数据，不作为默认 CI 门禁。
- Web 页面问题优先检查 API 响应、SSE 数据流和错误状态码。

## CI

GitHub Actions 会在 `main` 分支 push 和 pull request 时执行：

```bash
python -m pytest tests/unit
```

当前测试矩阵为 Python `3.11` 和 `3.12`。如果后续支持 Python `3.13`，需要先确认核心依赖和本地数据流程都兼容，再调整 `pyproject.toml` 与 CI 矩阵。

## 优化计划

优化路线和当前进度记录在：

```text
docs/claude_quant_optimization_plan.md
```

每次完成较大优化、修复关键问题或调整优先级后，都要同步更新该文件。
