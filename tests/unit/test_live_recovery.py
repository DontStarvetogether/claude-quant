from __future__ import annotations

from datetime import datetime

import pytest

from cq.live import LiveRecoveryState, LiveRecoveryStore


def test_live_recovery_store_saves_loads_lists_and_deletes_state(tmp_path):
    store = LiveRecoveryStore(tmp_path)
    first = LiveRecoveryState(
        session_id="session-1",
        status="running",
        updated_at=datetime(2024, 1, 2, 9, 30),
        idempotency_keys=("k1", "k2"),
        pending_plan_ids=("plan-1",),
        metadata={"mode": "paper"},
    )
    second = LiveRecoveryState(
        session_id="session-2",
        status="stopped",
        updated_at=datetime(2024, 1, 2, 15, 0),
    )

    path = store.save(first)
    store.save(second)

    assert path.exists()
    loaded = store.load("session-1")
    assert loaded == first
    assert [state.session_id for state in store.list_states()] == ["session-2", "session-1"]
    assert store.delete("session-1") is True
    assert store.load("session-1") is None
    assert store.delete("session-1") is False


def test_live_recovery_state_validates_schema():
    with pytest.raises(ValueError, match="unsupported recovery schema"):
        LiveRecoveryState.from_dict({"schema_version": "bad"})


def test_live_recovery_store_rejects_unsafe_empty_session_id(tmp_path):
    store = LiveRecoveryStore(tmp_path)

    with pytest.raises(ValueError, match="session_id"):
        store.save(
            LiveRecoveryState(
                session_id="///",
                status="running",
                updated_at=datetime(2024, 1, 2, 9, 30),
            )
        )
