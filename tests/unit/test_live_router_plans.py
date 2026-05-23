from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from cq.live import TradePlanStore
from web.routers import live
from web.schemas import LiveStartRequest, TradePlanCreateRequest, TradePlanReviewRequest


def _patch_store(tmp_path, monkeypatch) -> TradePlanStore:
    store = TradePlanStore(tmp_path / "plans")
    monkeypatch.setattr(live, "get_trade_plan_store", lambda: store)
    return store


def test_trade_plan_api_create_list_approve_and_reject(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)

    created = asyncio.run(
        live.create_trade_plan(
            TradePlanCreateRequest(
                plan_id="plan-1",
                trade_date="2024-01-02",
                strategy_id="double_ma",
                account_id="acct-1",
                orders=[
                    {
                        "trade_date": "2024-01-02",
                        "symbol": "600519.SH",
                        "side": "BUY",
                        "quantity": 100,
                    }
                ],
            )
        )
    )
    listed = asyncio.run(live.list_trade_plans())
    approved = asyncio.run(
        live.approve_trade_plan("plan-1", TradePlanReviewRequest(reviewer="tester"))
    )
    rejected = asyncio.run(
        live.reject_trade_plan(
            "plan-1",
            TradePlanReviewRequest(reviewer="tester", reason="bad price"),
        )
    )

    assert created.plan["status"] == "pending"
    assert listed.plans[0]["plan_id"] == "plan-1"
    assert approved.plan["status"] == "approved"
    assert rejected.plan["status"] == "rejected"
    assert rejected.plan["review_reason"] == "bad price"


def test_live_start_requires_approved_trade_plan_by_default(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            live.start_live(
                LiveStartRequest(
                    strategy_id="double_ma",
                    symbols=["600519.SH"],
                    mode="live",
                    account_id="acct-1",
                    initial_capital=1_000_000,
                )
            )
        )

    assert exc.value.status_code == 400
    assert "trade_plan_id" in exc.value.detail
