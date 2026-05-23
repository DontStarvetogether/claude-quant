from __future__ import annotations

from datetime import datetime

from cq.core.models import Bar
from cq.live import LiveRecoveryStore
from cq.live.engine import LiveEngine
from cq.strategy.base import Strategy
from cq.utils.config import Config
from web import live_runner
from web.live_runner import LiveSession, _configure_engine_state, _export_session_daily_report


class _NoopStrategy(Strategy):
    strategy_id = "noop"

    def on_bar(self, bar: Bar) -> None:
        return


def test_configure_engine_state_wires_idempotency_and_recovery(tmp_path):
    config = Config.default()
    config.data.root = str(tmp_path)
    session = LiveSession(
        session_id="session-1",
        strategy_id="noop",
        symbols=["600519.SH"],
        mode="paper",
        started_at=datetime(2024, 1, 2, 9, 30),
    )
    engine = LiveEngine(config)

    _configure_engine_state(engine, config, session)
    engine.add_strategy(_NoopStrategy(), symbols=session.symbols)

    assert engine._idempotency_store is not None
    assert engine._idempotency_store.register("intent-key") is True
    engine._save_recovery_state("running")

    state = LiveRecoveryStore(tmp_path / "live_state" / "recovery").load(session.session_id)
    assert state is not None
    assert state.status == "running"
    assert state.idempotency_keys == ("intent-key",)
    assert state.metadata["mode"] == "paper"
    assert state.metadata["strategy_id"] == "noop"
    assert state.metadata["symbols"] == ["600519.SH"]
    assert (tmp_path / "live_state" / "idempotency" / "session-1.json").exists()


def test_export_session_daily_report_writes_report_files(tmp_path, monkeypatch):
    config = Config.default()
    config.data.root = str(tmp_path)
    session = LiveSession(
        session_id="session-1",
        strategy_id="noop",
        symbols=["600519.SH"],
        mode="paper",
        current_date="2024-01-02",
        positions=[
            {
                "symbol": "600519.SH",
                "quantity": 100,
                "last_price": 101.0,
                "market_value": 10_100.0,
                "unrealized_pnl": 100.0,
            }
        ],
    )

    monkeypatch.setattr(
        live_runner.db,
        "get_trades",
        lambda session_id: [
            {
                "trade_date": "2024-01-02",
                "symbol": "600519.SH",
                "side": "BUY",
                "price": 100.0,
                "quantity": 100,
                "amount": 10_000.0,
                "commission": 5.0,
                "stamp_tax": 0.0,
            }
        ],
    )
    monkeypatch.setattr(
        live_runner.db,
        "get_equity_curve",
        lambda session_id: [
            {
                "trade_date": "2024-01-02",
                "total_assets": 1_000_100.0,
                "cash": 990_000.0,
                "position_value": 10_100.0,
            }
        ],
    )

    _export_session_daily_report(config, session)

    output_dir = tmp_path / "live_state" / "reports" / "session-1"
    assert (output_dir / "daily_report.md").exists()
    assert (output_dir / "daily_summary.json").exists()
    assert (output_dir / "trades.csv").exists()
