"""
配置管理。

优先级（高 → 低）：环境变量 > config/local.yaml > config/default.yaml
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml  # type: ignore[import-untyped]
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class EngineConfig:
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    min_commission: float = 5.0
    slippage: float = 0.0


@dataclass
class DataConfig:
    root: str = "~/.cq/data"
    source: str = "baostock"
    tushare_token: Optional[str] = field(default=None)

    def __post_init__(self) -> None:
        # 从环境变量读取 token
        if self.tushare_token is None:
            self.tushare_token = os.getenv("TUSHARE_TOKEN")

    @property
    def root_path(self) -> Path:
        return Path(self.root).expanduser()


@dataclass
class RiskConfig:
    max_position_pct: float = 0.20
    max_drawdown_stop: float = 0.15
    min_cash_reserve: float = 0.05
    max_daily_trades: int = 50


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Optional[str] = None
    rotation: str = "10 MB"


@dataclass
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    data: DataConfig = field(default_factory=DataConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def default(cls) -> "Config":
        return cls()

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """从 YAML 文件加载配置，合并到默认值。"""
        if not HAS_YAML:
            raise ImportError("请安装 pyyaml: pip install pyyaml")

        path = Path(path)
        if not path.exists():
            return cls.default()

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        cfg = cls.default()

        if engine_raw := raw.get("engine"):
            for k, v in engine_raw.items():
                if hasattr(cfg.engine, k):
                    setattr(cfg.engine, k, v)

        if data_raw := raw.get("data"):
            for k, v in data_raw.items():
                if hasattr(cfg.data, k):
                    setattr(cfg.data, k, v)

        if risk_raw := raw.get("risk"):
            for k, v in risk_raw.items():
                if hasattr(cfg.risk, k):
                    setattr(cfg.risk, k, v)

        if log_raw := raw.get("logging"):
            for k, v in log_raw.items():
                if hasattr(cfg.logging, k):
                    setattr(cfg.logging, k, v)

        # 环境变量覆盖
        cfg.data.__post_init__()

        return cfg
