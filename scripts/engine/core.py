"""
Self-Contained Temporal Inventory, Pricing, Reservation, Dispatch & JIT Engine
All monetary prices and inventory quantities use exact Decimal arithmetic (Invariant 7).
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PRICE_Q = Decimal("0.01")
QTY_Q = Decimal("0.000001")


def _to_dec(val: float | int | str | Decimal, quant: Optional[Decimal] = None) -> Decimal:
    d = val if isinstance(val, Decimal) else Decimal(str(val))
    return d.quantize(quant, rounding=ROUND_HALF_UP) if quant is not None else d


class MixedSKUOrderError(ValueError):
    """Raised when lots belonging to multiple SKUs are passed to a single-SKU dispatch call."""


class ReservationStateError(ValueError):
    """Raised when committing or releasing a reservation in an invalid state."""


class TemporalArchetype(Enum):
    TYPE_1_RAPID_SPOIL = "RAPID_SPOIL"
    TYPE_2_SHELF_DECAY = "SHELF_DECAY"
    TYPE_3_MATURING = "MATURING"
    TYPE_4_BELL_CURVE = "BELL_CURVE"
    TYPE_5_INERT = "INERT"

    @classmethod
    def parse(cls, raw: str) -> "TemporalArchetype":
        normalized = raw.strip().upper()
        for member in cls:
            if normalized in (member.name, member.value):
                return member
        raise ValueError(f"Unknown temporal archetype: {raw!r}")


class LotStatus(Enum):
    AGING_LOCKED = "AGING_LOCKED"
    PRIME_MATURED = "PRIME_MATURED"
    NORMAL_AVAILABLE = "NORMAL_AVAILABLE"
    WARNING_DISCOUNT = "WARNING_DISCOUNT"
    EXPIRED_LOCKED = "EXPIRED_LOCKED"


@dataclass
class InventoryItem:
    sku: str
    name: str
    archetype: TemporalArchetype
    base_cost: Decimal | float | int | str
    base_selling_price: Decimal | float | int | str
    maturation_days: int = 0
    peak_quality_days: int = 0
    appreciation_multiplier: float = 1.0
    shelf_life_days: Optional[int] = None
    warning_threshold_days: int = 7
    # Configurable physical/chemical curve models (never inferred from SKU string!)
    decay_model: str = "LINEAR"          # 'LINEAR', 'WEIBULL', 'MOISTURE_STRENGTH'
    decay_rate_k: float = 0.0035
    weibull_alpha: float = 0.002
    weibull_beta: float = 1.5
    relative_humidity: float = 65.0
    maturation_curve: str = "SATURATING" # 'SATURATING', 'LOGARITHMIC', 'SIGMOID'
    maturation_steepness: float = 3.0

    def __post_init__(self) -> None:
        self.base_cost = _to_dec(self.base_cost, QTY_Q)
        self.base_selling_price = _to_dec(self.base_selling_price, PRICE_Q)
        self.decay_model = self.decay_model.strip().upper()
        self.maturation_curve = self.maturation_curve.strip().upper()
        if self.decay_model not in {"LINEAR", "WEIBULL", "MOISTURE_STRENGTH", "NONE"}:
            raise ValueError(f"Unsupported decay_model: {self.decay_model!r}")
        if self.maturation_curve not in {"SATURATING", "LOGARITHMIC", "SIGMOID"}:
            raise ValueError(f"Unsupported maturation_curve: {self.maturation_curve!r}")


@dataclass
class TemperatureLog:
    recorded_at: datetime
    celsius: float


@dataclass
class StockLot:
    lot_id: str
    item: InventoryItem
    batch_number: str
    quantity: Decimal | float | int | str
    mfg_date: date
    storage_location: str
    allocated_quantity: Decimal | float | int | str = Decimal("0")
    temperature_logs: List[TemperatureLog] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.quantity = _to_dec(self.quantity, QTY_Q)
        self.allocated_quantity = _to_dec(self.allocated_quantity, QTY_Q)

    @property
    def available_quantity(self) -> Decimal:
        return max(Decimal("0"), self.quantity - self.allocated_quantity)

    def age_days(self, current_date: date) -> int:
        return max(0, (current_date - self.mfg_date).days)

    def expiry_date(self) -> Optional[date]:
        if self.item.shelf_life_days is not None:
            return self.mfg_date + timedelta(days=self.item.shelf_life_days)
        return None

    def days_to_expiry(self, current_date: date) -> Optional[int]:
        exp = self.expiry_date()
        return (exp - current_date).days if exp is not None else None


@dataclass
class ReservationLine:
    lot: StockLot
    quantity: Decimal
    unit_price: Decimal
    status_at_pick: str
    quality_score: float
    age_days: int


@dataclass
class StockReservation:
    reservation_id: str
    sku: str
    strategy: str
    lines: List[ReservationLine]
    created_at: datetime
    expires_at: datetime
    status: str = "ACTIVE"  # 'ACTIVE', 'COMMITTED', 'RELEASED', 'EXPIRED'


# ---------------------------------------------------------------------------
# Kinetics, Decay & Maturation Curve Functions
# ---------------------------------------------------------------------------

def cement_compressive_strength_mpa(
    storage_days: int,
    nominal_strength: float = 53.0,
    relative_humidity: float = 65.0,
    decay_rate_k: float = 0.0035,
) -> float:
    rh_factor = (relative_humidity / 100.0) ** 2
    return nominal_strength * math.exp(-decay_rate_k * storage_days * rh_factor)


def weibull_survival_quality(age_days: int, alpha: float = 0.002, beta: float = 1.5) -> float:
    if age_days <= 0:
        return 1.0
    return math.exp(-alpha * (age_days ** beta))


def compute_maturation_quality(
    age_days: int,
    target_mature_days: int = 365,
    curve: str = "SATURATING",
    steepness: float = 3.0,
) -> float:
    target = max(1, target_mature_days)
    c = curve.upper()
    if c == "LOGARITHMIC":
        # Oak-cask whisky/spirits: fast early extraction, slow logarithmic depth
        ratio = math.log1p(age_days) / math.log1p(target)
        return min(1.0, 0.4 + 0.6 * min(1.0, ratio))
    if c == "SIGMOID":
        # Timber kiln/air seasoning toward equilibrium moisture content
        midpoint = target * 0.5
        k = steepness / target * 2.0
        return 1.0 / (1.0 + math.exp(-k * (age_days - midpoint)))
    # Default 'SATURATING' (Von Bertalanffy grain/rice starch retrogradation)
    k_aging = steepness / target
    return 0.5 + 0.5 * (1.0 - math.exp(-k_aging * age_days))


def grain_rice_maturation_index(age_days: int, target_mature_days: int = 365) -> float:
    return compute_maturation_quality(age_days, target_mature_days, "SATURATING", 3.0)


def asymmetric_bell_quality(
    current_age_days: int, peak_age_days: int, sigma_growth: float, sigma_decay: float
) -> float:
    sigma = sigma_growth if current_age_days <= peak_age_days else sigma_decay
    return math.exp(-((current_age_days - peak_age_days) ** 2) / (2.0 * (sigma ** 2)))


def arrhenius_mkt_celsius(logs: List[TemperatureLog], activation_energy_kj: float = 83.144) -> float:
    if not logs:
        return 20.0
    r_const = 0.0083144
    dh_over_r = activation_energy_kj / r_const
    mean_exp = sum(math.exp(-dh_over_r / (entry.celsius + 273.15)) for entry in logs) / len(logs)
    return (dh_over_r / (-math.log(mean_exp))) - 273.15


def jit_dispatch_offset(
    scheduled_consumption: datetime,
    mean_transit_minutes: float,
    unload_minutes: float = 30.0,
    std_transit_minutes: float = 15.0,
    z_confidence: float = 1.96,
) -> datetime:
    total_lead_mins = mean_transit_minutes + unload_minutes + (z_confidence * std_transit_minutes)
    return scheduled_consumption - timedelta(minutes=total_lead_mins)


# ---------------------------------------------------------------------------
# Pricing & Status Evaluation (Exact Decimal Prices)
# ---------------------------------------------------------------------------

def evaluate_lot(lot: StockLot, current_date: date) -> Tuple[LotStatus, Decimal, float]:
    item = lot.item
    base_price: Decimal = _to_dec(item.base_selling_price, PRICE_Q)
    age = lot.age_days(current_date)
    days_to_exp = lot.days_to_expiry(current_date)

    if item.archetype == TemporalArchetype.TYPE_5_INERT:
        return LotStatus.NORMAL_AVAILABLE, base_price, 1.0

    if days_to_exp is not None and days_to_exp <= 0:
        return LotStatus.EXPIRED_LOCKED, Decimal("0.00"), 0.0

    if item.archetype in (TemporalArchetype.TYPE_1_RAPID_SPOIL, TemporalArchetype.TYPE_2_SHELF_DECAY):
        total_life = item.shelf_life_days or 30
        remaining_pct = max(0.0, (days_to_exp if days_to_exp is not None else total_life) / total_life)
        if item.decay_model == "MOISTURE_STRENGTH":
            quality_score = (
                cement_compressive_strength_mpa(
                    age, nominal_strength=53.0, relative_humidity=item.relative_humidity, decay_rate_k=item.decay_rate_k
                )
                / 53.0
            )
        elif item.decay_model == "WEIBULL":
            quality_score = weibull_survival_quality(age, item.weibull_alpha, item.weibull_beta)
        else:
            quality_score = remaining_pct

        if days_to_exp is not None and days_to_exp <= item.warning_threshold_days:
            urgency_ratio = Decimal("1") - (Decimal(days_to_exp) / Decimal(max(1, item.warning_threshold_days)))
            discount = min(Decimal("0.50"), urgency_ratio * Decimal("0.50"))
            dyn_price = (base_price * (Decimal("1") - discount)).quantize(PRICE_Q, rounding=ROUND_HALF_UP)
            return LotStatus.WARNING_DISCOUNT, dyn_price, quality_score
        return LotStatus.NORMAL_AVAILABLE, base_price, quality_score

    if item.archetype == TemporalArchetype.TYPE_3_MATURING:
        quality_score = compute_maturation_quality(
            age, item.maturation_days, item.maturation_curve, item.maturation_steepness
        )
        if age < item.maturation_days:
            return LotStatus.AGING_LOCKED, base_price, quality_score
        mature_progress = Decimal(str(min(1.0, (age - item.maturation_days) / max(1, item.maturation_days))))
        mult = _to_dec(item.appreciation_multiplier)
        markup = Decimal("1") + ((mult - Decimal("1")) * (Decimal("0.5") + Decimal("0.5") * mature_progress))
        dyn_price = (base_price * markup).quantize(PRICE_Q, rounding=ROUND_HALF_UP)
        return LotStatus.PRIME_MATURED, dyn_price, quality_score

    if item.archetype == TemporalArchetype.TYPE_4_BELL_CURVE:
        peak_age = item.peak_quality_days or max(1, item.maturation_days * 2)
        quality_score = asymmetric_bell_quality(age, peak_age, peak_age * 0.4, peak_age * 0.6)
        if age < item.maturation_days:
            return LotStatus.AGING_LOCKED, base_price, quality_score
        # Post-peak spoilage cliff: acetified/oxidized lots must lock, never sell forever!
        if age > peak_age and (quality_score < 0.15 or age > peak_age * 2.5):
            return LotStatus.EXPIRED_LOCKED, Decimal("0.00"), quality_score
        if abs(age - peak_age) <= (peak_age * 0.25):
            dyn_price = (base_price * _to_dec(item.appreciation_multiplier)).quantize(PRICE_Q, rounding=ROUND_HALF_UP)
            return LotStatus.PRIME_MATURED, dyn_price, quality_score
        if age > peak_age:
            md_factor = _to_dec(max(0.5, quality_score))
            dyn_price = (base_price * md_factor).quantize(PRICE_Q, rounding=ROUND_HALF_UP)
            return LotStatus.WARNING_DISCOUNT, dyn_price, quality_score
        return LotStatus.NORMAL_AVAILABLE, base_price, quality_score

    return LotStatus.NORMAL_AVAILABLE, base_price, 1.0


# ---------------------------------------------------------------------------
# Multi-Strategy Dispatch & Reservation Lifecycle
# ---------------------------------------------------------------------------

def strategy_name_for(archetype: TemporalArchetype) -> str:
    return {
        TemporalArchetype.TYPE_1_RAPID_SPOIL: "FEFO (First-Expired, First-Out)",
        TemporalArchetype.TYPE_2_SHELF_DECAY: "FEFO (First-Expired, First-Out)",
        TemporalArchetype.TYPE_3_MATURING: "FMFO (First-Matured, First-Out)",
        TemporalArchetype.TYPE_4_BELL_CURVE: "PEAK_WINDOW (Max Quality Score)",
        TemporalArchetype.TYPE_5_INERT: "LIFO (Top-of-Pile Bulk Scoop)",
    }[archetype]


def _validate_single_sku(lots: List[StockLot]) -> None:
    skus = {lot.item.sku for lot in lots}
    if len(skus) > 1:
        raise MixedSKUOrderError(
            f"Mixed SKUs passed to single-SKU dispatch ({sorted(skus)}); "
            "filter lots by SKU or call fulfill_multi_sku_order()."
        )


def get_dispatchable_queue(lots: List[StockLot], current_date: date) -> List[Dict[str, Any]]:
    if not lots:
        return []
    _validate_single_sku(lots)
    archetype = lots[0].item.archetype
    evaluated: List[Dict[str, Any]] = []
    for lot in lots:
        status, price, quality = evaluate_lot(lot, current_date)
        if lot.available_quantity > 0 and status not in (LotStatus.AGING_LOCKED, LotStatus.EXPIRED_LOCKED):
            evaluated.append({
                "lot": lot,
                "status": status,
                "price": price,
                "quality_score": quality,
                "available_qty": lot.available_quantity,
                "age_days": lot.age_days(current_date),
                "days_to_expiry": lot.days_to_expiry(current_date),
            })

    if archetype in (TemporalArchetype.TYPE_1_RAPID_SPOIL, TemporalArchetype.TYPE_2_SHELF_DECAY):
        evaluated.sort(key=lambda x: x["days_to_expiry"] if x["days_to_expiry"] is not None else 999999)
    elif archetype == TemporalArchetype.TYPE_3_MATURING:
        evaluated.sort(key=lambda x: x["age_days"], reverse=True)
    elif archetype == TemporalArchetype.TYPE_4_BELL_CURVE:
        evaluated.sort(key=lambda x: x["quality_score"], reverse=True)
    elif archetype == TemporalArchetype.TYPE_5_INERT:
        evaluated.sort(key=lambda x: x["lot"].mfg_date, reverse=True)
    return evaluated


def fulfill_order(
    lots: List[StockLot],
    requested_quantity: float | int | str | Decimal,
    current_date: date,
    ttl_minutes: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    req_qty = _to_dec(requested_quantity, QTY_Q)
    if req_qty <= 0:
        raise ValueError(f"requested_quantity must be > 0, got {requested_quantity!r}")
    _validate_single_sku(lots)

    strategy = strategy_name_for(lots[0].item.archetype) if lots else "NONE"
    queue = get_dispatchable_queue(lots, current_date)
    allocations: List[Dict[str, Any]] = []
    res_lines: List[ReservationLine] = []
    remaining = req_qty
    total_val = Decimal("0.00")

    for entry in queue:
        lot: StockLot = entry["lot"]
        pull_qty = min(remaining, entry["available_qty"])
        lot.allocated_quantity = _to_dec(lot.allocated_quantity + pull_qty, QTY_Q)
        remaining = _to_dec(remaining - pull_qty, QTY_Q)
        line_val = (pull_qty * entry["price"]).quantize(PRICE_Q, rounding=ROUND_HALF_UP)
        total_val += line_val

        res_lines.append(
            ReservationLine(
                lot=lot,
                quantity=pull_qty,
                unit_price=entry["price"],
                status_at_pick=entry["status"].value,
                quality_score=round(entry["quality_score"], 3),
                age_days=entry["age_days"],
            )
        )
        allocations.append({
            "lot_id": lot.lot_id,
            "batch_number": lot.batch_number,
            "quantity_picked": pull_qty,
            "unit_price": entry["price"],
            "status": entry["status"].value,
            "quality_score": round(entry["quality_score"], 3),
            "age_days": entry["age_days"],
        })
        if remaining <= 0:
            break

    ts_now = now or datetime.now(timezone.utc)
    reservation = StockReservation(
        reservation_id=f"RES-{uuid.uuid4().hex[:12].upper()}",
        sku=lots[0].item.sku if lots else "",
        strategy=strategy,
        lines=res_lines,
        created_at=ts_now,
        expires_at=ts_now + timedelta(minutes=ttl_minutes),
    )

    fulfilled = _to_dec(req_qty - remaining, QTY_Q)
    return {
        "reservation_id": reservation.reservation_id,
        "reservation": reservation,
        "sku": lots[0].item.sku if lots else None,
        "strategy": strategy,
        "requested_quantity": req_qty,
        "fulfilled_quantity": fulfilled,
        "is_fully_fulfilled": remaining <= 0,
        "unfulfilled_quantity": max(Decimal("0"), remaining),
        "total_order_value": total_val.quantize(PRICE_Q, rounding=ROUND_HALF_UP),
        "allocations": allocations,
    }


def _extract_reservation(reservation_or_result: StockReservation | Dict[str, Any]) -> StockReservation:
    if isinstance(reservation_or_result, StockReservation):
        return reservation_or_result
    if isinstance(reservation_or_result, dict) and isinstance(reservation_or_result.get("reservation"), StockReservation):
        return reservation_or_result["reservation"]
    raise TypeError("Expected StockReservation or fulfill_order() result dictionary")


def commit_allocation(
    reservation_or_result: StockReservation | Dict[str, Any],
    ledger: Optional[Any] = None,
    reference_doc: str = "DISPATCH_COMMIT",
    actor_id: str = "system",
    idempotency_prefix: Optional[str] = None,
    wbs_task_code: Optional[str] = None,
) -> StockReservation:
    """Commits an active reservation, deducting on-hand lot quantities and optionally posting ISSUE_OUT ledger rows."""
    res = _extract_reservation(reservation_or_result)
    if res.status != "ACTIVE":
        raise ReservationStateError(f"Cannot commit reservation {res.reservation_id} in state {res.status!r}")

    prefix = idempotency_prefix or res.reservation_id
    for idx, line in enumerate(res.lines):
        if ledger is not None:
            ledger.post_movement(
                idempotency_key=f"{prefix}:LINE:{idx}",
                sku=res.sku,
                lot_id=line.lot.lot_id,
                movement_type="ISSUE_OUT",
                signed_base_qty=-line.quantity,
                reference_doc=reference_doc,
                actor_id=actor_id,
                wbs_task_code=wbs_task_code,
            )
        line.lot.allocated_quantity = _to_dec(line.lot.allocated_quantity - line.quantity, QTY_Q)
        line.lot.quantity = _to_dec(line.lot.quantity - line.quantity, QTY_Q)

    res.status = "COMMITTED"
    return res


def release_allocation(reservation_or_result: StockReservation | Dict[str, Any]) -> StockReservation:
    """Releases reserved quantities back to available stock without mutating on-hand balances."""
    res = _extract_reservation(reservation_or_result)
    if res.status in ("RELEASED", "EXPIRED"):
        return res
    if res.status == "COMMITTED":
        raise ReservationStateError(f"Cannot release already-committed reservation {res.reservation_id}")

    for line in res.lines:
        line.lot.allocated_quantity = max(
            Decimal("0"), _to_dec(line.lot.allocated_quantity - line.quantity, QTY_Q)
        )
    res.status = "RELEASED"
    return res


cancel_order = release_allocation


def expire_stale_reservations(
    reservations: List[StockReservation], now: Optional[datetime] = None
) -> List[StockReservation]:
    ts_now = now or datetime.now(timezone.utc)
    expired: List[StockReservation] = []
    for res in reservations:
        if res.status == "ACTIVE" and ts_now >= res.expires_at:
            release_allocation(res)
            res.status = "EXPIRED"
            expired.append(res)
    return expired


def fulfill_multi_sku_order(
    all_lots: List[StockLot],
    order_lines: Dict[str, float | int | str | Decimal],
    current_date: date,
    ttl_minutes: int = 30,
) -> Dict[str, Dict[str, Any]]:
    """Routes a multi-line order so each SKU executes its own archetype dispatch strategy."""
    results: Dict[str, Dict[str, Any]] = {}
    for sku, qty in order_lines.items():
        sku_lots = [lot for lot in all_lots if lot.item.sku == sku]
        results[sku] = fulfill_order(sku_lots, qty, current_date, ttl_minutes=ttl_minutes)
    return results


def load_catalog_file(path: Path) -> Tuple[date, Dict[str, InventoryItem], List[StockLot]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ref_date = (
        date.fromisoformat(data["reference_date"])
        if "reference_date" in data
        else date.today()
    )
    items: Dict[str, InventoryItem] = {}
    for raw_item in data.get("items", []):
        item = InventoryItem(
            sku=raw_item["sku"],
            name=raw_item["name"],
            archetype=TemporalArchetype.parse(raw_item["archetype"]),
            base_cost=raw_item["base_cost"],
            base_selling_price=raw_item["base_selling_price"],
            maturation_days=int(raw_item.get("maturation_days", 0)),
            peak_quality_days=int(raw_item.get("peak_quality_days", 0)),
            appreciation_multiplier=float(raw_item.get("appreciation_multiplier", 1.0)),
            shelf_life_days=raw_item.get("shelf_life_days"),
            warning_threshold_days=int(raw_item.get("warning_threshold_days", 7)),
            decay_model=raw_item.get("decay_model", "LINEAR"),
            decay_rate_k=float(raw_item.get("decay_rate_k", 0.0035)),
            weibull_alpha=float(raw_item.get("weibull_alpha", 0.002)),
            weibull_beta=float(raw_item.get("weibull_beta", 1.5)),
            relative_humidity=float(raw_item.get("relative_humidity", 65.0)),
            maturation_curve=raw_item.get("maturation_curve", "SATURATING"),
            maturation_steepness=float(raw_item.get("maturation_steepness", 3.0)),
        )
        items[item.sku] = item

    lots: List[StockLot] = []
    for raw_lot in data.get("lots", []):
        sku = raw_lot["sku"]
        lots.append(
            StockLot(
                lot_id=raw_lot["lot_id"],
                item=items[sku],
                batch_number=raw_lot["batch_number"],
                quantity=raw_lot["quantity"],
                mfg_date=date.fromisoformat(raw_lot["mfg_date"]),
                storage_location=raw_lot.get("storage_location", "DEFAULT"),
            )
        )
    return ref_date, items, lots
