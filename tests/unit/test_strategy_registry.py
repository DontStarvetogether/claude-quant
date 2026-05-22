"""单元测试：策略注册表。"""

import pytest

from cq.strategy.registry import STRATEGY_METADATA, load_strategy, validate_strategy_params


def test_registry_params_are_accepted_by_strategy_instances():
    for strategy_id, meta in STRATEGY_METADATA.items():
        params = {p["name"]: p["default"] for p in meta["params"]}
        strategy = load_strategy(strategy_id, params)
        strategy.on_init()
        strategy._apply_configured_params()

        for key, value in params.items():
            assert hasattr(strategy, key), f"{strategy_id} 缺少参数 {key}"
            assert getattr(strategy, key) == value


def test_validate_strategy_params_rejects_unknown_param():
    with pytest.raises(ValueError, match="不支持参数"):
        validate_strategy_params("double_ma", {"fast": 5, "ghost": 1})
