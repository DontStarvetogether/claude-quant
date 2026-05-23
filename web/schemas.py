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


# ── 股票池 ───────────────────────────────────────────────────────────────────


class UniverseInfo(BaseModel):
    id: str
    name: str
    source: str = "builtin_preset"
    construction: str = "static"
    symbols: list[str]


class UniversesResponse(BaseModel):
    universes: list[UniverseInfo]


# ── 回测请求 ─────────────────────────────────────────────────────────────────


class RiskParams(BaseModel):
    max_position_pct: float = Field(default=0.20, ge=0.01, le=1.0)
    min_cash_reserve: float = Field(default=0.05, ge=0.0, le=0.5)
    max_drawdown_stop: float = Field(default=0.15, ge=0.01, le=1.0)


class BacktestUniverse(BaseModel):
    universe_id: str = "custom_static"
    universe_name: str = "自定义静态股票池"
    source: str = "user_selection"
    construction: str = "static"


class BacktestRequest(BaseModel):
    strategy_id: str
    symbols: list[str] = Field(min_length=1)
    start_date: str     # "YYYY-MM-DD"
    end_date: str       # "YYYY-MM-DD"
    initial_capital: float = Field(default=1_000_000, ge=10_000)
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskParams = Field(default_factory=RiskParams)
    slippage: float = Field(default=0.0, ge=0.0, le=0.01)
    adjust: str = Field(default="dynamic", pattern="^(qfq|dynamic)$")
    enable_capacity_limit: bool = True
    max_volume_participation: float = Field(default=0.10, ge=0.0, le=1.0)
    universe: Optional[BacktestUniverse] = None
    benchmark: Optional[str] = None   # 基准指数代码，如 "000300.SH"


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
    profit_factor: Optional[float] = None
    avg_hold_days: float
    fill_count: int = 0
    round_trip_count: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    avg_daily_turnover: float = 0.0
    annual_turnover: float = 0.0
    max_daily_turnover: float = 0.0
    buy_turnover: float = 0.0
    sell_turnover: float = 0.0
    total_turnover: float = 0.0
    gross_return: float = 0.0
    net_return: float = 0.0
    gross_annual_return: float = 0.0
    net_annual_return: float = 0.0
    total_slippage_cost: float = 0.0
    cost_drag: float = 0.0
    cost_to_nav: float = 0.0
    avg_position_count: float = 0.0
    max_position_count: int = 0
    min_position_count: int = 0
    avg_cash_ratio: float = 0.0
    average_exposure: float = 0.0
    max_single_position_weight: float = 0.0
    avg_top5_concentration: float = 0.0
    total_fees: float
    total_commission: float  # 总佣金
    total_stamp_tax: float  # 总印花税
    final_value: float
    final_cash: float  # 最终持有现金
    final_position_value: float  # 最终持仓市值
    initial_value: float
    max_drawdown_start: Optional[str] = None
    max_drawdown_end: Optional[str] = None
    # 基准对比
    excess_return: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0
    information_ratio: float = 0.0
    tracking_error: float = 0.0
    benchmark_return: float = 0.0
    benchmark_annual_return: float = 0.0


class EquityCurveData(BaseModel):
    dates: list[str]
    values: list[float]
    drawdown: list[float]


class BenchmarkDiagnostics(BaseModel):
    sample_days: int = 0
    missing_days: int = 0
    win_days: int = 0
    hit_rate: float = 0.0
    avg_daily_excess: float = 0.0
    relative_return: float = 0.0
    common_start: Optional[str] = None
    common_end: Optional[str] = None
    aligned: bool = False


class SymbolDataDiagnostic(BaseModel):
    symbol: str
    role: str = "trade_symbol"
    status: str
    new_records: int = 0
    used_cache: bool = False
    local_first_date: Optional[str] = None
    local_last_date: Optional[str] = None
    requested_start: Optional[str] = None
    requested_end: Optional[str] = None
    error: Optional[str] = None
    list_date: Optional[str] = None
    coverage_status: str = "unknown"
    source: Optional[str] = None
    cache_path: Optional[str] = None
    cache_updated_at: Optional[str] = None
    raw_first_date: Optional[str] = None
    raw_last_date: Optional[str] = None
    qfq_first_date: Optional[str] = None
    qfq_last_date: Optional[str] = None
    factor_first_date: Optional[str] = None
    factor_last_date: Optional[str] = None
    qfq_available: bool = False
    factor_available: bool = False
    st_status_source: str = "unavailable"
    limit_price_source: str = "unknown"
    repair_actions: list[str] = Field(default_factory=list)
    quality_level: str = "unknown"
    data_quality: Optional[dict[str, Any]] = None


class DataDiagnosticsSummary(BaseModel):
    total: int = 0
    updated: int = 0
    cache_hit: int = 0
    failed: int = 0
    missing: int = 0


class DataDiagnostics(BaseModel):
    symbols: list[SymbolDataDiagnostic] = Field(default_factory=list)
    benchmark: Optional[SymbolDataDiagnostic] = None
    summary: DataDiagnosticsSummary = Field(default_factory=DataDiagnosticsSummary)


class UniverseDiagnostics(BaseModel):
    universe_id: str = "custom_static"
    universe_name: str = "自定义静态股票池"
    source: str = "user_selection"
    construction: str = "static"
    selection_time: str = "run_submit"
    symbol_count: int = 0
    survivorship_bias_risk: str = "unknown"
    universe_type: str = "static_builtin"
    point_in_time_available: bool = False
    history_membership_available: bool = False
    point_in_time: bool = False
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    requested_quantity: Optional[int] = None
    filled_quantity: Optional[int] = None
    fill_ratio: float = 1.0
    capacity_limited: bool = False
    capacity_limit_qty: Optional[int] = None
    reject_reason: Optional[str] = None
    fee: float = 0.0
    slippage_adjusted_price: Optional[float] = None


class BacktestResultResponse(BaseModel):
    run_id: str
    strategy_name: str
    symbols: list[str]
    start_date: str
    end_date: str
    initial_capital: float
    benchmark: Optional[str] = None
    benchmark_symbol: Optional[str] = None
    benchmark_name: Optional[str] = None
    benchmark_status: str = "not_requested"
    benchmark_error: Optional[str] = None
    alpha_beta_available: bool = False
    benchmark_curve_available: bool = False
    engine_version: Optional[str] = None
    execution_model: Optional[str] = None
    data_quality: Optional[dict[str, Any]] = None
    execution_diagnostics: Optional[dict[str, Any]] = None
    execution_assumptions: Optional[dict[str, Any]] = None
    metric_diagnostics: Optional[dict[str, Any]] = None
    risk_events: list[dict[str, Any]] = Field(default_factory=list)
    benchmark_diagnostics: Optional[BenchmarkDiagnostics] = None
    data_diagnostics: Optional[DataDiagnostics] = None
    universe_diagnostics: Optional[UniverseDiagnostics] = None
    metrics: MetricsDict
    equity_curve: EquityCurveData
    benchmark_curve: Optional[EquityCurveData] = None
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
    require_trade_plan: Optional[bool] = None  # None=实盘默认要求，模拟盘默认不要求
    trade_plan_id: Optional[str] = None
    kill_switch_enabled: Optional[bool] = None
    kill_switch_reason: Optional[str] = None
    daily_loss_limit_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    daily_loss_limit_amount: Optional[float] = Field(default=None, ge=0.0)


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


class TradePlanOrderRequest(BaseModel):
    symbol: str
    side: str = Field(pattern="^(BUY|SELL)$")
    quantity: int = Field(default=0, ge=0)
    trade_date: str
    order_type: str = Field(default="MARKET", pattern="^(MARKET|LIMIT)$")
    namespace: str = "web"
    limit_price: Optional[float] = None
    percent: Optional[float] = None
    amount: Optional[float] = None


class TradePlanCreateRequest(BaseModel):
    trade_date: str
    strategy_id: str
    account_id: str
    orders: list[TradePlanOrderRequest] = Field(default_factory=list)
    plan_id: Optional[str] = None


class TradePlanReviewRequest(BaseModel):
    reviewer: str = "web"
    reason: str = ""


class TradePlanResponse(BaseModel):
    plan: dict[str, Any]


class TradePlanListResponse(BaseModel):
    plans: list[dict[str, Any]]
