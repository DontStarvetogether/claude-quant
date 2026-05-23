"""
配置管理。

优先级（高 → 低）：环境变量 > config/local.yaml > config/default.yaml
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml  # type: ignore[import-untyped]
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class EngineConfig:
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.00015
    stamp_tax_rate: float = 0.0005
    min_commission: float = 5.0
    slippage: float = 0.0
    adjust: str = "dynamic"  # "dynamic"=动态复权（推荐）, "qfq"=静态前复权
    enable_capacity_limit: bool = True
    max_volume_participation: float = 0.10


@dataclass
class DataConfig:
    root: str = "~/.cq/data"
    source: str = "akshare"
    tushare_token: str | None = field(default=None)

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
class LiveConfig:
    """实盘相关配置（QMT）。"""
    account_id: str = ""
    mini_qmt_dir: str = "C:/国金证券QMT交易端/userdata_mini"
    bar_period: str = "1d"     # 行情周期："1d" 日线，"1m" 分钟线
    history_days: int = 300    # 预加载历史天数（用于策略指标计算）
    session_id: int = 1        # QMT session_id（同一账号多个策略时需区分）


@dataclass
class LiveSafetyConfig:
    """实盘安全阈值。"""

    require_trade_plan: bool = True
    kill_switch_enabled: bool = False
    kill_switch_reason: str = ""
    daily_loss_limit_pct: float = 0.0
    daily_loss_limit_amount: float = 0.0


@dataclass
class LiveAlertsConfig:
    """实盘报警通道。密钥和 webhook URL 允许由环境变量注入。"""

    jsonl_path: str | None = None
    webhook_url: str | None = None
    feishu_webhook_url: str | None = None
    wecom_webhook_url: str | None = None

    def __post_init__(self) -> None:
        self.webhook_url = self.webhook_url or os.getenv("CQ_ALERT_WEBHOOK_URL")
        self.feishu_webhook_url = self.feishu_webhook_url or os.getenv("CQ_ALERT_FEISHU_WEBHOOK_URL")
        self.wecom_webhook_url = self.wecom_webhook_url or os.getenv("CQ_ALERT_WECOM_WEBHOOK_URL")


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str | None = None
    rotation: str = "10 MB"


@dataclass
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    data: DataConfig = field(default_factory=DataConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    live_safety: LiveSafetyConfig = field(default_factory=LiveSafetyConfig)
    live_alerts: LiveAlertsConfig = field(default_factory=LiveAlertsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def default(cls) -> Config:
        return cls()

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
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

        if live_raw := raw.get("live"):
            for k, v in live_raw.items():
                if hasattr(cfg.live, k):
                    setattr(cfg.live, k, v)

        if live_safety_raw := raw.get("live_safety"):
            for k, v in live_safety_raw.items():
                if hasattr(cfg.live_safety, k):
                    setattr(cfg.live_safety, k, v)

        if live_alerts_raw := raw.get("live_alerts"):
            for k, v in live_alerts_raw.items():
                if hasattr(cfg.live_alerts, k):
                    setattr(cfg.live_alerts, k, v)

        if log_raw := raw.get("logging"):
            for k, v in log_raw.items():
                if hasattr(cfg.logging, k):
                    setattr(cfg.logging, k, v)

        # 环境变量覆盖
        cfg.data.__post_init__()
        cfg.live_alerts.__post_init__()

        return cfg
