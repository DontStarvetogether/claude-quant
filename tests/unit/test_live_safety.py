from __future__ import annotations

from datetime import date, datetime

from cq.core.event_bus import EventBus
from cq.core.events import FillEvent, RejectEvent, SignalEvent
from cq.core.models import Bar, OrderSide, OrderType, Signal
from cq.engine.portfolio import PortfolioManager
from cq.execution.paper import PaperExecutor
from cq.live import (
    DailyLossGuard,
    KillSwitch,
    OrderIdempotencyStore,
    OrderIntent,
    TradePlan,
)
from cq.risk.pre_trade import PreTradeRisk
from cq.utils.config import EngineConfig, RiskConfig


def _signal(signal_id: str = "S1") -> Signal:
    return Signal(
        signal_id=signal_id,
        symbol="600519.SH",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        percent=0.1,
    )


def test_order_intent_key_is_stable_for_same_intent():
    first = OrderIntent.from_signal(
        _signal("S1"),
        quantity=0,
        trade_date=date(2024, 1, 2),
        namespace="paper",
    )
    second = OrderIntent.from_signal(
        _signal("S2"),
        quantity=0,
        trade_date=date(2024, 1, 2),
        namespace="paper",
    )

    assert first.key == second.key
    assert len(first.key) == 24


def test_order_idempotency_store_registers_and_persists_keys(tmp_path):
    path = tmp_path / "idempotency.json"
    store = OrderIdempotencyStore(path)

    assert store.register("abc") is True
    assert store.register("abc") is False
    assert store.seen("abc") is True

    restored = OrderIdempotencyStore(path)
    assert restored.seen("abc") is True


def test_trade_plan_requires_manual_approval():
    intent = OrderIntent.from_signal(
        _signal(),
        quantity=0,
        trade_date=date(2024, 1, 2),
    )
    plan = TradePlan(
        plan_id="plan-1",
        trade_date=date(2024, 1, 2),
        strategy_id="demo",
        account_id="paper",
        orders=(intent,),
        generated_at=datetime(2024, 1, 2, 15, 0),
    )

    try:
        plan.require_approved()
    except PermissionError as exc:
        assert "not approved" in str(exc)
    else:
        raise AssertionError("pending plan should not be approved")

    approved = plan.approve("tester", reviewed_at=datetime(2024, 1, 2, 15, 1))
    approved.require_approved()
    assert approved.status == "approved"
    assert approved.to_dict()["orders"][0]["idempotency_key"] == intent.key

    rejected = plan.reject("tester", "bad price")
    assert rejected.status == "rejected"
    assert rejected.review_reason == "bad price"


def test_kill_switch_and_daily_loss_guard():
    assert KillSwitch().check().passed is True
    blocked = KillSwitch(enabled=True, reason="maintenance").check()
    assert blocked.passed is False
    assert blocked.reason == "maintenance"

    guard = DailyLossGuard(max_loss_pct=0.05, max_loss_amount=100_000)
    assert guard.check(start_assets=1_000_000, current_assets=980_000).passed is True
    result = guard.check(start_assets=1_000_000, current_assets=940_000)
    assert result.passed is False
    assert "daily loss pct" in result.reason


def test_paper_executor_rejects_duplicate_order_intent():
    bus = EventBus()
    portfolio = PortfolioManager(EngineConfig(initial_capital=1_000_000))
    portfolio.update_prices([
        Bar(
            symbol="600519.SH",
            trade_date=date(2024, 1, 2),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1_000_000,
            amount=100_000_000,
            limit_up=110,
            limit_down=90,
            pre_close=100,
        )
    ])
    risk = PreTradeRisk(portfolio, RiskConfig())
    store = OrderIdempotencyStore()
    executor = PaperExecutor(bus, portfolio, risk, idempotency_store=store)
    executor.set_current_date(date(2024, 1, 2))

    executor.on_signal(SignalEvent(signal=_signal("S1")))
    first = executor.event_queue.get_nowait()
    executor.on_signal(SignalEvent(signal=_signal("S2")))
    second = executor.event_queue.get_nowait()

    assert isinstance(first, FillEvent)
    assert isinstance(second, RejectEvent)
    assert "重复订单已拦截" in second.reason
