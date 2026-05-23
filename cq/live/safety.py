"""Safety primitives for paper/live trading workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from cq.core.models import OrderSide, OrderType, Signal

SCHEMA_VERSION = "live_safety.v1"


@dataclass(frozen=True)
class SafetyCheckResult:
    """Result returned by safety guards."""

    passed: bool
    reason: str = ""
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class OrderIntent:
    """Stable order intent used to build idempotency keys."""

    namespace: str
    trade_date: date
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: float | None = None
    percent: float | None = None
    amount: float | None = None

    @classmethod
    def from_signal(
        cls,
        signal: Signal,
        *,
        quantity: int,
        trade_date: date,
        namespace: str = "default",
    ) -> OrderIntent:
        return cls(
            namespace=namespace,
            trade_date=trade_date,
            symbol=signal.symbol.upper(),
            side=signal.side,
            order_type=signal.order_type,
            quantity=quantity,
            limit_price=signal.limit_price,
            percent=signal.percent,
            amount=signal.amount,
        )

    @property
    def key(self) -> str:
        payload = {
            "namespace": self.namespace,
            "trade_date": self.trade_date.isoformat(),
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "percent": self.percent,
            "amount": self.amount,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "trade_date": self.trade_date.isoformat(),
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "percent": self.percent,
            "amount": self.amount,
            "idempotency_key": self.key,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OrderIntent:
        return cls(
            namespace=str(payload.get("namespace", "default")),
            trade_date=_parse_date(payload["trade_date"]),
            symbol=str(payload["symbol"]).upper(),
            side=OrderSide(str(payload["side"])),
            order_type=OrderType(str(payload.get("order_type", OrderType.MARKET.value))),
            quantity=int(payload.get("quantity", 0)),
            limit_price=_optional_float(payload.get("limit_price")),
            percent=_optional_float(payload.get("percent")),
            amount=_optional_float(payload.get("amount")),
        )


class OrderIdempotencyStore:
    """Track submitted order intent keys, optionally persisted as JSON."""

    def __init__(self, path: str | Path | None = None, keys: set[str] | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._keys: set[str] = set(keys or set())
        self._lock = Lock()
        if self._path is not None and self._path.exists():
            self._keys.update(self._load_keys(self._path))

    def seen(self, key: str) -> bool:
        with self._lock:
            return key in self._keys

    def register(self, key: str) -> bool:
        """Register a key. Return False if it has already been registered."""
        with self._lock:
            if key in self._keys:
                return False
            self._keys.add(key)
            self._persist()
            return True

    def keys(self) -> set[str]:
        with self._lock:
            return set(self._keys)

    @staticmethod
    def _load_keys(path: Path) -> set[str]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return set(str(key) for key in payload.get("keys", []))

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, "keys": sorted(self._keys)}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class TradePlan:
    """Manual-confirmation trade plan."""

    plan_id: str
    trade_date: date
    strategy_id: str
    account_id: str
    orders: tuple[OrderIntent, ...]
    generated_at: datetime
    status: str = "pending"
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_reason: str = ""

    def approve(self, reviewer: str, reviewed_at: datetime | None = None) -> TradePlan:
        return replace(
            self,
            status="approved",
            reviewed_by=reviewer,
            reviewed_at=reviewed_at or datetime.now(),
            review_reason="",
        )

    def reject(
        self,
        reviewer: str,
        reason: str,
        reviewed_at: datetime | None = None,
    ) -> TradePlan:
        return replace(
            self,
            status="rejected",
            reviewed_by=reviewer,
            reviewed_at=reviewed_at or datetime.now(),
            review_reason=reason,
        )

    def require_approved(self) -> None:
        if self.status != "approved":
            raise PermissionError(f"trade plan {self.plan_id} is not approved")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trade_date"] = self.trade_date.isoformat()
        payload["generated_at"] = self.generated_at.isoformat()
        payload["reviewed_at"] = self.reviewed_at.isoformat() if self.reviewed_at else None
        payload["orders"] = [order.to_dict() for order in self.orders]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TradePlan:
        return cls(
            plan_id=str(payload["plan_id"]),
            trade_date=_parse_date(payload["trade_date"]),
            strategy_id=str(payload["strategy_id"]),
            account_id=str(payload["account_id"]),
            orders=tuple(OrderIntent.from_dict(order) for order in payload.get("orders", [])),
            generated_at=_parse_datetime(payload["generated_at"]),
            status=str(payload.get("status", "pending")),
            reviewed_by=_optional_str(payload.get("reviewed_by")),
            reviewed_at=_parse_optional_datetime(payload.get("reviewed_at")),
            review_reason=str(payload.get("review_reason", "")),
        )


class TradePlanStore:
    """Persist manual-confirmation trade plans as JSON files."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._lock = Lock()

    def save(self, plan: TradePlan) -> None:
        path = self._path(plan.plan_id)
        payload = {"schema_version": SCHEMA_VERSION, "plan": plan.to_dict()}
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    def load(self, plan_id: str) -> TradePlan | None:
        path = self._path(plan_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported trade plan schema: {payload.get('schema_version')}")
        return TradePlan.from_dict(payload["plan"])

    def list_plans(self, *, status: str | None = None) -> list[TradePlan]:
        if not self._root.exists():
            return []
        plans: list[TradePlan] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                plan = TradePlan.from_dict(payload["plan"])
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
            if status is None or plan.status == status:
                plans.append(plan)
        return sorted(plans, key=lambda plan: plan.generated_at, reverse=True)

    def approve(
        self,
        plan_id: str,
        *,
        reviewer: str,
        reviewed_at: datetime | None = None,
    ) -> TradePlan:
        plan = self._require(plan_id)
        approved = plan.approve(reviewer, reviewed_at=reviewed_at)
        self.save(approved)
        return approved

    def reject(
        self,
        plan_id: str,
        *,
        reviewer: str,
        reason: str,
        reviewed_at: datetime | None = None,
    ) -> TradePlan:
        plan = self._require(plan_id)
        rejected = plan.reject(reviewer, reason, reviewed_at=reviewed_at)
        self.save(rejected)
        return rejected

    def _require(self, plan_id: str) -> TradePlan:
        plan = self.load(plan_id)
        if plan is None:
            raise KeyError(f"trade plan not found: {plan_id}")
        return plan

    def _path(self, plan_id: str) -> Path:
        safe = _safe_id(plan_id)
        return self._root / f"{safe}.json"


@dataclass(frozen=True)
class KillSwitch:
    """Global switch to block new order submission."""

    enabled: bool = False
    reason: str = ""

    def check(self) -> SafetyCheckResult:
        if not self.enabled:
            return SafetyCheckResult(passed=True)
        return SafetyCheckResult(passed=False, reason=self.reason or "kill switch enabled")


@dataclass(frozen=True)
class DailyLossGuard:
    """Block trading when daily loss exceeds configured thresholds."""

    max_loss_pct: float = 0.0
    max_loss_amount: float = 0.0

    def check(self, *, start_assets: float, current_assets: float) -> SafetyCheckResult:
        if start_assets <= 0:
            return SafetyCheckResult(passed=False, reason="start_assets must be positive")
        loss = max(start_assets - current_assets, 0.0)
        loss_pct = loss / start_assets
        details = {
            "start_assets": round(start_assets, 2),
            "current_assets": round(current_assets, 2),
            "loss": round(loss, 2),
            "loss_pct": round(loss_pct, 6),
        }
        if self.max_loss_amount > 0 and loss >= self.max_loss_amount:
            return SafetyCheckResult(
                passed=False,
                reason=f"daily loss amount limit reached: {loss:.2f} >= {self.max_loss_amount:.2f}",
                details=details,
            )
        if self.max_loss_pct > 0 and loss_pct >= self.max_loss_pct:
            return SafetyCheckResult(
                passed=False,
                reason=f"daily loss pct limit reached: {loss_pct:.2%} >= {self.max_loss_pct:.2%}",
                details=details,
            )
        return SafetyCheckResult(passed=True, details=details)


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _parse_datetime(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _safe_id(value: str) -> str:
    plan_id = str(value).strip()
    if not plan_id or "/" in plan_id or "\\" in plan_id or plan_id in {".", ".."}:
        raise ValueError(f"unsafe trade plan id: {value!r}")
    return plan_id
