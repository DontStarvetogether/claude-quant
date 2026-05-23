"""
实盘/模拟盘会话管理器。

LiveEngine.paper_trade() / run() 是同步阻塞的，在独立线程中执行。
通过 EventBus 订阅事件，将实时状态写入 LiveSession（内存共享）。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from loguru import logger

from cq.core.events import BarEvent, EndOfDayEvent, FillEvent
from cq.core.models import OrderSide, Trade
from cq.data.store.parquet_store import ParquetStore
from cq.live import (
    AlertLevel,
    AlertManager,
    DailyLossGuard,
    FeishuAlertSink,
    KillSwitch,
    JsonlAlertSink,
    LiveRecoveryStore,
    OrderIdempotencyStore,
    TradePlanStore,
    WeComAlertSink,
    WebhookAlertSink,
    export_daily_trading_report,
    generate_daily_trading_report,
)
from cq.live.engine import LiveEngine
from cq.performance.metrics import PerformanceMetrics
from cq.strategy.registry import load_strategy
from cq.utils.config import Config
from web import db
from web.runner import _ensure_data


@dataclass
class LiveSession:
    session_id: str
    strategy_id: str
    symbols: list[str]
    mode: str                                    # "paper"
    status: str = "starting"                     # starting | running | stopped | failed
    started_at: datetime = field(default_factory=datetime.now)

    # 实时状态（主线程读，引擎线程写，CPython GIL 保护）
    current_date: str | None = None
    total_assets: float | None = None
    cash: float | None = None
    initial_capital: float = 1_000_000
    positions: list[dict] = field(default_factory=list)
    recent_trades: deque[dict] = field(default_factory=lambda: deque(maxlen=50))
    elapsed_seconds: float = 0.0
    error: str | None = None

    # 内部引用（不序列化）
    engine: LiveEngine | None = field(default=None, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    _start_time: float = field(default=0.0, repr=False)


class LiveSessionStore:
    """单例内存存储。"""
    _instance: LiveSessionStore | None = None

    def __new__(cls) -> LiveSessionStore:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions: dict[str, LiveSession] = {}
        return cls._instance

    def create(self, **kwargs: Any) -> LiveSession:
        session = LiveSession(**kwargs)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> LiveSession | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def all(self) -> list[LiveSession]:
        return sorted(self._sessions.values(), key=lambda s: s.started_at, reverse=True)


live_store = LiveSessionStore()


def start_paper_session(
    strategy_id: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
    config_path: str = "config/local.yaml",
) -> LiveSession:
    """启动模拟盘会话，返回 LiveSession。"""
    session_id = uuid.uuid4().hex[:12]
    session = live_store.create(
        session_id=session_id,
        strategy_id=strategy_id,
        symbols=symbols,
        mode="paper",
        initial_capital=initial_capital,
    )

    db.save_session(
        session_id=session_id,
        strategy_id=strategy_id,
        symbols=symbols,
        mode="paper",
        initial_capital=initial_capital,
        start_date=start_date,
        end_date=end_date,
        started_at=session.started_at,
    )

    t = threading.Thread(
        target=_run_paper,
        args=(session, start_date, end_date, strategy_params, risk_params, config_path),
        daemon=True,
        name=f"live-paper-{session_id}",
    )
    session.thread = t
    t.start()
    return session


def start_live_session(
    strategy_id: str,
    symbols: list[str],
    initial_capital: float,
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
    account_id: str,
    mini_qmt_dir: str | None = None,
    trade_plan_id: str | None = None,
    kill_switch_enabled: bool | None = None,
    kill_switch_reason: str | None = None,
    daily_loss_limit_pct: float | None = None,
    daily_loss_limit_amount: float | None = None,
    config_path: str = "config/local.yaml",
) -> LiveSession:
    """启动实盘会话，返回 LiveSession。"""
    session_id = uuid.uuid4().hex[:12]
    session = live_store.create(
        session_id=session_id,
        strategy_id=strategy_id,
        symbols=symbols,
        mode="live",
        initial_capital=initial_capital,
    )

    db.save_session(
        session_id=session_id,
        strategy_id=strategy_id,
        symbols=symbols,
        mode="live",
        initial_capital=initial_capital,
        start_date="",
        end_date="",
        started_at=session.started_at,
    )

    t = threading.Thread(
        target=_run_live,
        args=(
            session,
            account_id,
            mini_qmt_dir,
            strategy_params,
            risk_params,
            config_path,
            trade_plan_id,
            kill_switch_enabled,
            kill_switch_reason,
            daily_loss_limit_pct,
            daily_loss_limit_amount,
        ),
        daemon=True,
        name=f"live-real-{session_id}",
    )
    session.thread = t
    t.start()
    return session


def stop_session(session_id: str) -> bool:
    """停止指定会话。"""
    session = live_store.get(session_id)
    if session is None or session.engine is None:
        return False
    session.engine.stop()
    session.status = "stopped"
    return True


# ── 内部实现 ──────────────────────────────────────────────────────────────────


def _configure_engine_state(
    engine: LiveEngine,
    config: Config,
    session: LiveSession,
    *,
    account_id: str | None = None,
    trade_plan_id: str | None = None,
    kill_switch_enabled: bool | None = None,
    kill_switch_reason: str | None = None,
    daily_loss_limit_pct: float | None = None,
    daily_loss_limit_amount: float | None = None,
) -> None:
    """Configure persistent safety/recovery state for Web-started sessions."""
    state_root = config.data.root_path / "live_state"
    idempotency_store = OrderIdempotencyStore(
        state_root / "idempotency" / f"{session.session_id}.json"
    )
    recovery_store = LiveRecoveryStore(state_root / "recovery")
    safety_cfg = config.live_safety
    kill_enabled = safety_cfg.kill_switch_enabled if kill_switch_enabled is None else kill_switch_enabled
    kill_reason = safety_cfg.kill_switch_reason if kill_switch_reason is None else kill_switch_reason
    loss_pct = safety_cfg.daily_loss_limit_pct if daily_loss_limit_pct is None else daily_loss_limit_pct
    loss_amount = (
        safety_cfg.daily_loss_limit_amount
        if daily_loss_limit_amount is None
        else daily_loss_limit_amount
    )
    engine.configure_safety(
        idempotency_store=idempotency_store,
        kill_switch=KillSwitch(enabled=kill_enabled, reason=kill_reason),
        daily_loss_guard=DailyLossGuard(
            max_loss_pct=loss_pct,
            max_loss_amount=loss_amount,
        ),
    )
    engine.configure_recovery(
        recovery_store=recovery_store,
        session_id=session.session_id,
        metadata={
            "mode": session.mode,
            "strategy_id": session.strategy_id,
            "symbols": list(session.symbols),
            "account_id": account_id or "",
            "started_at": session.started_at.isoformat(),
            "trade_plan_id": trade_plan_id or "",
        },
        pending_plan_ids=(trade_plan_id,) if trade_plan_id else (),
    )


def _export_session_daily_report(
    config: Config,
    session: LiveSession,
    alerts: list[str] | None = None,
    alert_manager: AlertManager | None = None,
) -> None:
    """Export a daily report for a finished Web live/paper session."""
    try:
        trade_date = session.current_date or date.today().isoformat()
        report = generate_daily_trading_report(
            session_id=session.session_id,
            trade_date=trade_date,
            trades=db.get_trades(session.session_id),
            equity_curve=db.get_equity_curve(session.session_id),
            positions=session.positions,
            alerts=alerts,
        )
        output_dir = config.data.root_path / "live_state" / "reports" / session.session_id
        export_daily_trading_report(report, output_dir)
    except Exception as exc:
        logger.warning(f"导出每日交易日报失败 session={session.session_id}: {exc}")
        if alert_manager is not None:
            alert_manager.send(
                level=AlertLevel.WARNING,
                title="交易日报导出失败",
                message=str(exc),
                source="web.live_runner",
                session_id=session.session_id,
            )


def load_recovery_snapshot(
    session_id: str,
    config_path: str = "config/local.yaml",
) -> dict[str, Any] | None:
    """Load a persisted recovery snapshot for API display."""
    config = Config.from_yaml(config_path)
    state = LiveRecoveryStore(config.data.root_path / "live_state" / "recovery").load(session_id)
    return state.to_dict() if state is not None else None


def list_recovery_snapshots(config_path: str = "config/local.yaml") -> list[dict[str, Any]]:
    """List persisted recovery snapshots for API display."""
    config = Config.from_yaml(config_path)
    store = LiveRecoveryStore(config.data.root_path / "live_state" / "recovery")
    return [state.to_dict() for state in store.list_states()]


def load_daily_report_snapshot(
    session_id: str,
    config_path: str = "config/local.yaml",
) -> dict[str, Any] | None:
    """Load a persisted daily report summary and markdown for API display."""
    config = Config.from_yaml(config_path)
    report_dir = config.data.root_path / "live_state" / "reports" / session_id
    summary_path = report_dir / "daily_summary.json"
    report_path = report_dir / "daily_report.md"
    if not summary_path.exists() or not report_path.exists():
        return None
    return {
        "session_id": session_id,
        "summary": json.loads(summary_path.read_text(encoding="utf-8")),
        "markdown": report_path.read_text(encoding="utf-8"),
        "files": {
            "report": str(report_path),
            "summary": str(summary_path),
            "trades": str(report_dir / "trades.csv"),
            "positions": str(report_dir / "positions.csv"),
        },
    }


def list_daily_report_snapshots(config_path: str = "config/local.yaml") -> list[dict[str, Any]]:
    """List persisted daily report summaries for API display."""
    config = Config.from_yaml(config_path)
    report_root = config.data.root_path / "live_state" / "reports"
    if not report_root.exists():
        return []

    reports: list[dict[str, Any]] = []
    for summary_path in sorted(
        report_root.glob("*/daily_summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        session_id = summary_path.parent.name
        report_path = summary_path.parent / "daily_report.md"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        reports.append(
            {
                "session_id": session_id,
                "trade_date": summary.get("trade_date"),
                "summary": summary,
                "files": {
                    "report": str(report_path),
                    "summary": str(summary_path),
                    "trades": str(summary_path.parent / "trades.csv"),
                    "positions": str(summary_path.parent / "positions.csv"),
                },
            }
        )
    return reports


def get_trade_plan_store(config_path: str = "config/local.yaml") -> TradePlanStore:
    """Return the persistent trade plan store for Web APIs."""
    config = Config.from_yaml(config_path)
    return TradePlanStore(config.data.root_path / "live_state" / "plans")


def _build_alert_manager(config: Config) -> AlertManager | None:
    sinks = []
    alerts = config.live_alerts
    if alerts.jsonl_path:
        sinks.append(JsonlAlertSink(Path(alerts.jsonl_path).expanduser()))
    if alerts.webhook_url:
        sinks.append(WebhookAlertSink(alerts.webhook_url))
    if alerts.feishu_webhook_url:
        sinks.append(FeishuAlertSink(alerts.feishu_webhook_url))
    if alerts.wecom_webhook_url:
        sinks.append(WeComAlertSink(alerts.wecom_webhook_url))
    return AlertManager(sinks) if sinks else None


def _run_paper(
    session: LiveSession,
    start_date: str,
    end_date: str,
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
    config_path: str,
) -> None:
    """在独立线程中执行模拟盘（同步阻塞）。"""
    session._start_time = time.monotonic()
    alert_manager: AlertManager | None = None
    try:
        config = Config.from_yaml(config_path)
        alert_manager = _build_alert_manager(config)
        config.engine.initial_capital = session.initial_capital
        config.risk.max_position_pct = risk_params.get("max_position_pct", 0.20)
        config.risk.min_cash_reserve = risk_params.get("min_cash_reserve", 0.05)

        # 确保数据已下载
        _ensure_data(config, session.symbols, start_date, end_date,
                     _DummyRecord(session), session._start_time)

        strategy = load_strategy(session.strategy_id, strategy_params)

        store = ParquetStore(config.data.root_path)
        engine = LiveEngine(config)
        _configure_engine_state(engine, config, session)
        engine.add_strategy(strategy, symbols=session.symbols)
        session.engine = engine

        # 启动引擎（会初始化 _portfolio 和 _bus）
        # 我们用 _hook_events 在引擎构建组件后注入事件监听
        original_build = engine._build_components

        def hooked_build(*args, **kwargs):
            result = original_build(*args, **kwargs)
            bus, portfolio, risk, ctx = result
            _hook_events(bus, session, engine)
            session.status = "running"
            session.total_assets = session.initial_capital
            session.cash = session.initial_capital
            return result

        engine._build_components = hooked_build

        engine.paper_trade(
            store=store,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
        )

        # 正常结束
        _sync_portfolio_state(session, engine)
        if session.status != "stopped":
            session.status = "stopped"
        session.elapsed_seconds = time.monotonic() - session._start_time

        metrics_payload = _compute_metrics(session.session_id)
        metrics_json = json.dumps(metrics_payload, ensure_ascii=False) if metrics_payload else None

        db.update_session_status(
            session.session_id, "stopped",
            total_assets=session.total_assets, cash=session.cash,
            elapsed_seconds=session.elapsed_seconds,
            positions_json=_serialize_positions(session),
            metrics_json=metrics_json,
        )
        _export_session_daily_report(config, session, alert_manager=alert_manager)
        logger.info(f"模拟盘结束 session={session.session_id}")

    except Exception as e:
        session.status = "failed"
        session.error = str(e)
        session.elapsed_seconds = time.monotonic() - session._start_time

        metrics_payload = _compute_metrics(session.session_id)
        metrics_json = json.dumps(metrics_payload, ensure_ascii=False) if metrics_payload else None

        db.update_session_status(session.session_id, "failed", error=str(e),
                                 elapsed_seconds=session.elapsed_seconds,
                                 positions_json=_serialize_positions(session),
                                 metrics_json=metrics_json)
        if alert_manager is not None:
            alert_manager.send(
                level=AlertLevel.ERROR,
                title="模拟盘会话异常",
                message=str(e),
                source="web.live_runner",
                session_id=session.session_id,
            )
        logger.error(f"模拟盘异常 session={session.session_id}: {e}")


def _run_live(
    session: LiveSession,
    account_id: str,
    mini_qmt_dir: str | None,
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
    config_path: str,
    trade_plan_id: str | None,
    kill_switch_enabled: bool | None,
    kill_switch_reason: str | None,
    daily_loss_limit_pct: float | None,
    daily_loss_limit_amount: float | None,
) -> None:
    """在独立线程中执行实盘交易（同步阻塞）。"""
    session._start_time = time.monotonic()
    alert_manager: AlertManager | None = None
    try:
        config = Config.from_yaml(config_path)
        alert_manager = _build_alert_manager(config)
        config.engine.initial_capital = session.initial_capital
        config.risk.max_position_pct = risk_params.get("max_position_pct", 0.20)
        config.risk.min_cash_reserve = risk_params.get("min_cash_reserve", 0.05)

        # QMT 实盘配置
        config.live.account_id = account_id
        if mini_qmt_dir:
            config.live.mini_qmt_dir = mini_qmt_dir

        strategy = load_strategy(session.strategy_id, strategy_params)

        engine = LiveEngine(config)
        _configure_engine_state(
            engine,
            config,
            session,
            account_id=account_id,
            trade_plan_id=trade_plan_id,
            kill_switch_enabled=kill_switch_enabled,
            kill_switch_reason=kill_switch_reason,
            daily_loss_limit_pct=daily_loss_limit_pct,
            daily_loss_limit_amount=daily_loss_limit_amount,
        )
        engine.add_strategy(strategy, symbols=session.symbols)
        session.engine = engine

        # hook _build_components 以注入事件监听
        original_build = engine._build_components

        def hooked_build(*args, **kwargs):
            result = original_build(*args, **kwargs)
            bus, portfolio, risk, ctx = result
            _hook_events(bus, session, engine)
            session.status = "running"
            session.total_assets = session.initial_capital
            session.cash = session.initial_capital
            return result

        engine._build_components = hooked_build

        # 启动实盘引擎（阻塞，直到 stop() 或异常）
        engine.run()

        # 正常结束
        _sync_portfolio_state(session, engine)
        if session.status != "stopped":
            session.status = "stopped"
        session.elapsed_seconds = time.monotonic() - session._start_time

        metrics_payload = _compute_metrics(session.session_id)
        metrics_json = json.dumps(metrics_payload, ensure_ascii=False) if metrics_payload else None

        db.update_session_status(
            session.session_id, "stopped",
            total_assets=session.total_assets, cash=session.cash,
            elapsed_seconds=session.elapsed_seconds,
            positions_json=_serialize_positions(session),
            metrics_json=metrics_json,
        )
        _export_session_daily_report(config, session, alert_manager=alert_manager)
        logger.info(f"实盘结束 session={session.session_id}")

    except Exception as e:
        session.status = "failed"
        session.error = str(e)
        session.elapsed_seconds = time.monotonic() - session._start_time

        metrics_payload = _compute_metrics(session.session_id)
        metrics_json = json.dumps(metrics_payload, ensure_ascii=False) if metrics_payload else None

        db.update_session_status(session.session_id, "failed", error=str(e),
                                 elapsed_seconds=session.elapsed_seconds,
                                 positions_json=_serialize_positions(session),
                                 metrics_json=metrics_json)
        if alert_manager is not None:
            alert_manager.send(
                level=AlertLevel.ERROR,
                title="实盘会话异常",
                message=str(e),
                source="web.live_runner",
                session_id=session.session_id,
            )
        logger.error(f"实盘异常 session={session.session_id}: {e}")


def _hook_events(bus, session: LiveSession, engine: LiveEngine) -> None:
    """订阅 EventBus 事件，更新 LiveSession 状态。"""

    def on_fill(event: FillEvent) -> None:
        t = event.trade
        trade_dict = {
            "trade_id": t.trade_id,
            "symbol": t.symbol,
            "side": t.side.value,
            "price": round(t.price, 4),
            "quantity": t.quantity,
            "amount": round(t.amount, 2),
            "commission": round(t.commission, 2),
            "stamp_tax": round(t.stamp_tax, 2),
            "net_amount": round(t.net_amount, 2),
            "trade_date": str(t.trade_date),
        }
        session.recent_trades.appendleft(trade_dict)
        db.save_trade(session.session_id, trade_dict)
        _sync_portfolio_state(session, engine)

    def on_eod(event: EndOfDayEvent) -> None:
        session.current_date = str(event.trade_date)
        session.elapsed_seconds = time.monotonic() - session._start_time
        _sync_portfolio_state(session, engine)
        # 每日净值快照
        portfolio = engine._portfolio
        if portfolio:
            total = portfolio.get_total_assets()
            cash = portfolio.get_cash()
            db.save_equity_snapshot(
                session.session_id, str(event.trade_date),
                total, cash, total - cash,
            )

    def on_bar(event: BarEvent) -> None:
        session.current_date = str(event.bar.trade_date)
        _sync_portfolio_state(session, engine)

    bus.subscribe(FillEvent, on_fill)
    bus.subscribe(EndOfDayEvent, on_eod)
    bus.subscribe(BarEvent, on_bar)


def _sync_portfolio_state(session: LiveSession, engine: LiveEngine) -> None:
    """从引擎的 portfolio 同步最新状态到 session。"""
    portfolio = engine._portfolio
    if portfolio is None:
        return
    session.total_assets = portfolio.get_total_assets()
    session.cash = portfolio.get_cash()
    positions = portfolio.get_all_positions()
    session.positions = [
        {
            "symbol": p.symbol,
            "total_qty": p.total_qty,
            "tradeable_qty": p.tradeable_qty,
            "avg_cost": round(p.avg_cost, 4),
            "last_price": round(p.last_price, 4),
            "market_value": round(p.market_value, 2),
            "unrealized_pnl": round(p.unrealized_pnl, 2),
            "unrealized_pnl_pct": round(p.unrealized_pnl_pct, 4),
        }
        for p in positions.values()
    ]


def _compute_metrics(session_id: str) -> dict | None:
    """从 DB 加载净值曲线和成交记录，计算绩效指标。
    返回 {"metrics": {...}, "equity": {dates/values/drawdown}} 或 None。"""
    from datetime import datetime

    import pandas as pd

    equity_rows = db.get_equity_curve(session_id)
    trade_rows = db.get_trades(session_id)

    if not equity_rows or len(equity_rows) < 2:
        return None

    # 构建 equity_curve (pd.Series)
    dates = [r["trade_date"] for r in equity_rows]
    values = [r["total_assets"] for r in equity_rows]
    equity_curve = pd.Series(values, index=pd.to_datetime(dates))

    # 构建 Trade 对象列表
    trades: list[Trade] = []
    for t in trade_rows:
        try:
            trade = Trade(
                trade_id=t.get("trade_id", ""),
                order_id=t.get("trade_id", ""),
                symbol=t["symbol"],
                side=OrderSide.BUY if t["side"] == "BUY" else OrderSide.SELL,
                quantity=t["quantity"],
                price=t["price"],
                amount=t["amount"],
                commission=t.get("commission", 0),
                stamp_tax=t.get("stamp_tax", 0),
                trade_time=datetime.fromisoformat(t["trade_date"]),
                trade_date=datetime.fromisoformat(t["trade_date"]).date(),
            )
            trades.append(trade)
        except Exception:
            pass

    try:
        metrics = PerformanceMetrics().compute(equity_curve, trades)
    except Exception as e:
        logger.error(f"计算绩效指标失败 session={session_id}: {e}")
        return None

    # 指标字典
    metrics_dict = metrics.to_dict()
    metrics_dict["max_drawdown_start"] = (
        str(metrics.max_drawdown_start) if metrics.max_drawdown_start else None
    )
    metrics_dict["max_drawdown_end"] = (
        str(metrics.max_drawdown_end) if metrics.max_drawdown_end else None
    )

    # 最终净值拆分
    last_row = equity_rows[-1]
    metrics_dict["final_cash"] = last_row.get("cash", 0)
    metrics_dict["final_position_value"] = last_row.get("position_value", 0)

    # 费用拆分
    metrics_dict["total_commission"] = round(sum(
        t.get("commission", 0) for t in trade_rows
    ), 2)
    metrics_dict["total_stamp_tax"] = round(sum(
        t.get("stamp_tax", 0) for t in trade_rows
    ), 2)

    # 净值曲线 + 回撤序列（供前端图表使用）
    rolling_max = equity_curve.cummax()
    drawdown = ((equity_curve - rolling_max) / rolling_max).fillna(0)

    equity_data = {
        "dates": [str(d.date()) if hasattr(d, 'date') else str(d) for d in equity_curve.index],
        "values": [round(v, 2) for v in equity_curve.values],
        "drawdown": [round(float(d), 6) for d in drawdown.values],
    }

    return {"metrics": metrics_dict, "equity": equity_data}


def _serialize_positions(session: LiveSession) -> str:
    """将当前持仓序列化为 JSON 字符串，用于持久化到 DB。"""
    return json.dumps(session.positions, ensure_ascii=False)


class _DummyRecord:
    """适配 _ensure_data 需要的 record 接口。"""
    def __init__(self, session: LiveSession) -> None:
        self._session = session

    @property
    def run_id(self):
        return self._session.session_id

    @property
    def current_date(self):
        return self._session.current_date

    @current_date.setter
    def current_date(self, v):
        self._session.current_date = v

    @property
    def elapsed_seconds(self):
        return self._session.elapsed_seconds

    @elapsed_seconds.setter
    def elapsed_seconds(self, v):
        self._session.elapsed_seconds = v
