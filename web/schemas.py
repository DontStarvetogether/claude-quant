"""API 请求/响应 Pydantic 模型"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── 策略 ─────────────────────────────────────────────────────────────────────


class StrategyParam(BaseModel):
    name: str
    type: str           # "int" | "float" | "bool"
    default: Any
    label: str
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None


class StrategyInfo(BaseModel):
    id: str
    name: str
    description: str
    params: list[StrategyParam]


class StrategiesResponse(BaseModel):
    strategies: list[StrategyInfo]


# ── 回测请求 ─────────────────────────────────────────────────────────────────


class RiskParams(BaseModel):
    max_position_pct: float = Field(default=0.20, ge=0.01, le=1.0)
    min_cash_reserve: float = Field(default=0.05, ge=0.0, le=0.5)
    max_drawdown_stop: float = Field(default=0.15, ge=0.01, le=1.0)


class BacktestRequest(BaseModel):
    strategy_id: str
    symbols: list[str] = Field(min_length=1)
    start_date: str     # "YYYY-MM-DD"
    end_date: str       # "YYYY-MM-DD"
    initial_capital: float = Field(default=1_000_000, ge=10_000)
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskParams = Field(default_factory=RiskParams)
    slippage: float = Field(default=0.0, ge=0.0, le=0.01)
    adjust: str = Field(default="qfq", pattern="^(qfq|dynamic)$")


class BacktestSubmitResponse(BaseModel):
    run_id: str
    status: str


# ── 运行状态 ─────────────────────────────────────────────────────────────────


class RunStatus(BaseModel):
    run_id: str
    status: str         # "pending" | "running" | "completed" | "failed"
    progress: int       # 0-100
    current_date: Optional[str] = None
    total_assets: Optional[float] = None
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


# ── 回测结果 ─────────────────────────────────────────────────────────────────


class MetricsDict(BaseModel):
    total_return: float
    annual_return: float
    max_drawdown: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    total_trades: int
    win_rate: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    avg_hold_days: float
    total_fees: float
    total_commission: float  # 总佣金
    total_stamp_tax: float  # 总印花税
    final_value: float
    final_cash: float  # 最终持有现金
    final_position_value: float  # 最终持仓市值
    initial_value: float
    max_drawdown_start: Optional[str] = None
    max_drawdown_end: Optional[str] = None


class EquityCurveData(BaseModel):
    dates: list[str]
    values: list[float]
    drawdown: list[float]


class TradeRecord(BaseModel):
    trade_id: str
    symbol: str
    side: str
    trade_date: str
    price: float
    quantity: int
    amount: float
    commission: float
    stamp_tax: float
    net_amount: float
    cash_after: float  # 交易后持有现金


class BacktestResultResponse(BaseModel):
    run_id: str
    strategy_name: str
    symbols: list[str]
    start_date: str
    end_date: str
    initial_capital: float
    metrics: MetricsDict
    equity_curve: EquityCurveData
    trades: list[TradeRecord]
    rejected_count: int
    created_at: str


# ── 历史记录 ─────────────────────────────────────────────────────────────────


class RunSummary(BaseModel):
    run_id: str
    strategy_name: str
    symbols: list[str]
    start_date: str
    end_date: str
    status: str
    total_return: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    created_at: str


class HistoryResponse(BaseModel):
    runs: list[RunSummary]


# ── 股票池 ────────────────────────────────────────────────────────────────────


class SymbolInfo(BaseModel):
    symbol: str
    name: str


class SymbolsResponse(BaseModel):
    symbols: list[SymbolInfo]
    total: int


# ── 实盘/模拟盘 ──────────────────────────────────────────────────────────────


class LiveStartRequest(BaseModel):
    strategy_id: str
    symbols: list[str] = Field(min_length=1)
    mode: str = "paper"      # "paper" | "live"
    start_date: Optional[str] = None    # 模拟盘回放起始日（模拟盘必填）
    end_date: Optional[str] = None      # 模拟盘回放结束日（模拟盘必填）
    initial_capital: float = Field(default=1_000_000, ge=10_000)
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskParams = Field(default_factory=RiskParams)
    # 实盘专用配置
    account_id: Optional[str] = None    # QMT 资金账号（实盘必填）
    mini_qmt_dir: Optional[str] = None  # QMT 数据目录（实盘可选，有默认值）


class LiveStartResponse(BaseModel):
    session_id: str
    status: str


class LivePositionItem(BaseModel):
    symbol: str
    total_qty: int
    tradeable_qty: int
    avg_cost: float
    last_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


class LiveTradeItem(BaseModel):
    trade_id: str
    symbol: str
    side: str
    price: float
    quantity: int
    amount: float
    commission: float
    stamp_tax: float
    net_amount: float
    trade_date: str


class LiveSessionStatus(BaseModel):
    session_id: str
    strategy_id: str
    symbols: list[str]
    mode: str = "paper"      # "paper" | "live"
    status: str              # "starting" | "running" | "stopped" | "failed"
    current_date: Optional[str] = None
    total_assets: Optional[float] = None
    cash: Optional[float] = None
    positions: list[LivePositionItem] = Field(default_factory=list)
    recent_trades: list[LiveTradeItem] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    initial_capital: float = 1_000_000
    metrics: Optional[dict] = None
    equity_curve: Optional[dict] = None   # { dates: [...], values: [...], drawdown: [...] }
    error: Optional[str] = None
    started_at: Optional[str] = None


class LiveSessionSummary(BaseModel):
    session_id: str
    strategy_id: str
    symbols: list[str]
    mode: str = "paper"      # "paper" | "live"
    status: str
    total_assets: Optional[float] = None
    elapsed_seconds: float = 0.0
    started_at: Optional[str] = None


class LiveSessionsResponse(BaseModel):
    sessions: list[LiveSessionSummary]
