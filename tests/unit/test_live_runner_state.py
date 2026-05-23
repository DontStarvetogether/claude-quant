from __future__ import annotations

from datetime import datetime

from cq.core.models import Bar
from cq.live import LiveRecoveryStore
from cq.live.engine import LiveEngine
from cq.strategy.base import Strategy
from cq.utils.config import Config
from web.live_runner import LiveSession, _configure_engine_state


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
