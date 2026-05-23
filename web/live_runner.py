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
from typing import Any, Optional

from loguru import logger

from cq.core.events import BarEvent, EndOfDayEvent, FillEvent, RejectEvent
from cq.core.models import OrderSide, Trade
from cq.data.store.parquet_store import ParquetStore
from cq.live.engine import LiveEngine
from cq.performance.metrics import PerformanceMetrics
from cq.strategy.registry import load_strategy
from cq.utils.config import Config
from web.runner import _ensure_data
from web import db


@dataclass
class LiveSession:
    session_id: str
    strategy_id: str
    symbols: list[str]
    mode: str                                    # "paper"
    status: str = "starting"                     # starting | running | stopped | failed
    started_at: datetime = field(default_factory=datetime.now)

    # 实时状态（主线程读，引擎线程写，CPython GIL 保护）
    current_date: Optional[str] = None
    total_assets: Optional[float] = None
    cash: Optional[float] = None
    initial_capital: float = 1_000_000
    positions: list[dict] = field(default_factory=list)
    recent_trades: deque[dict] = field(default_factory=lambda: deque(maxlen=50))
    elapsed_seconds: float = 0.0
    error: Optional[str] = None

    # 内部引用（不序列化）
    engine: Optional[LiveEngine] = field(default=None, repr=False)
    thread: Optional[threading.Thread] = field(default=None, repr=False)
    _start_time: float = field(default=0.0, repr=False)


class LiveSessionStore:
    """单例内存存储。"""
    _instance: Optional[LiveSessionStore] = None

    def __new__(cls) -> LiveSessionStore:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions: dict[str, LiveSession] = {}
        return cls._instance

    def create(self, **kwargs: Any) -> LiveSession:
        session = LiveSession(**kwargs)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Optional[LiveSession]:
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
    mini_qmt_dir: Optional[str] = None,
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
        args=(session, account_id, mini_qmt_dir, strategy_params, risk_params, config_path),
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
    try:
        config = Config.from_yaml(config_path)
        config.engine.initial_capital = session.initial_capital
        config.risk.max_position_pct = risk_params.get("max_position_pct", 0.20)
        config.risk.min_cash_reserve = risk_params.get("min_cash_reserve", 0.05)

        # 确保数据已下载
        _ensure_data(config, session.symbols, start_date, end_date,
                     _DummyRecord(session), session._start_time)

        strategy = load_strategy(session.strategy_id, strategy_params)

        store = ParquetStore(config.data.root_path)
        engine = LiveEngine(config)
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
        logger.error(f"模拟盘异常 session={session.session_id}: {e}")


def _run_live(
    session: LiveSession,
    account_id: str,
    mini_qmt_dir: Optional[str],
    strategy_params: dict[str, Any],
    risk_params: dict[str, Any],
    config_path: str,
) -> None:
    """在独立线程中执行实盘交易（同步阻塞）。"""
    session._start_time = time.monotonic()
    try:
        config = Config.from_yaml(config_path)
        config.engine.initial_capital = session.initial_capital
        config.risk.max_position_pct = risk_params.get("max_position_pct", 0.20)
        config.risk.min_cash_reserve = risk_params.get("min_cash_reserve", 0.05)

        # QMT 实盘配置
        config.live.account_id = account_id
        if mini_qmt_dir:
            config.live.mini_qmt_dir = mini_qmt_dir

        strategy = load_strategy(session.strategy_id, strategy_params)

        engine = LiveEngine(config)
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
    import json
    import pandas as pd
    from datetime import datetime

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
