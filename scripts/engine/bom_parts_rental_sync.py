"""
Multi-Level Kitchen/Factory BOM, Auto-Parts Interchange, Equipment Rental,
Multi-Currency Tax-Stripped Landed Cost & Offline Sync Conflict Resolution Engine.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .erp_ledger import ImmutableStockLedger


Q6 = Decimal("0.000001")
P2 = Decimal("0.01")


def _d(val: float | int | str | Decimal) -> Decimal:
    return val if isinstance(val, Decimal) else Decimal(str(val))


# ---------------------------------------------------------------------------
# 1. Restaurant / Bakery / Manufacturing Multi-Level BOM & Yield Rollup
# ---------------------------------------------------------------------------

@dataclass
class PrepIngredientInput:
    sku: str
    lot_id: str
    raw_input_base_qty: Decimal | float | str


def produce_prep_or_subassembly_batch(
    ledger: ImmutableStockLedger,
    work_order_id: str,
    inputs: Sequence[PrepIngredientInput],
    output_sku: str,
    output_lot_id: str,
    expected_raw_sum_qty: float | str | Decimal,
    yield_pct: float | str | Decimal,
    direct_conversion_cost: float | str | Decimal = "0.00",
    actor_id: str = "kitchen-prod",
) -> Dict[str, Any]:
    """
    Executes a multi-level kitchen prep or shop-floor sub-assembly run:
    1. Issues raw/intermediate component lots from ledger at their exact MWAC.
    2. Applies peeling/trimming/evaporation yield_pct to compute net output batch quantity.
    3. Rolls up total ingredient COGS + direct conversion cost into the output lot's unit cost.
    """
    y_pct = _d(yield_pct)
    if y_pct <= 0 or y_pct > 150:
        raise ValueError(f"yield_pct must be in (0, 150], got {yield_pct}")
    if not inputs:
        raise ValueError("inputs cannot be empty")

    output_qty = (_d(expected_raw_sum_qty) * (y_pct / Decimal("100"))).quantize(Q6, rounding=ROUND_HALF_UP)
    if output_qty <= 0:
        raise ValueError("Computed output_qty must be > 0")

    total_ingredient_cost = Decimal("0")
    consumed_entries = []
    for idx, inp in enumerate(inputs):
        raw_q = _d(inp.raw_input_base_qty).quantize(Q6, rounding=ROUND_HALF_UP)
        e = ledger.post_movement(
            idempotency_key=f"{work_order_id}:INP:{idx}",
            sku=inp.sku,
            lot_id=inp.lot_id,
            movement_type="ISSUE_OUT",
            signed_base_qty=-raw_q,
            reference_doc=work_order_id,
            actor_id=actor_id,
        )
        total_ingredient_cost += abs(e.signed_base_qty) * e.unit_cost_applied
        consumed_entries.append(e)

    total_batch_cost = total_ingredient_cost + _d(direct_conversion_cost)
    rolled_unit_cost = (total_batch_cost / output_qty).quantize(Q6, rounding=ROUND_HALF_UP)

    out_entry = ledger.post_movement(
        idempotency_key=f"{work_order_id}:OUT",
        sku=output_sku,
        lot_id=output_lot_id,
        movement_type="RECEIPT_GRN",
        signed_base_qty=output_qty,
        inbound_landed_unit_cost=rolled_unit_cost,
        reference_doc=work_order_id,
        actor_id=actor_id,
    )
    return {
        "work_order_id": work_order_id,
        "output_sku": output_sku,
        "output_lot_id": output_lot_id,
        "output_qty": output_qty,
        "rolled_up_unit_cost": rolled_unit_cost,
        "total_batch_cost": total_batch_cost.quantize(P2, rounding=ROUND_HALF_UP),
        "consumed_entries": len(consumed_entries),
        "output_ledger_id": out_entry.ledger_id,
    }


# ---------------------------------------------------------------------------
# 2. Auto Parts / Hardware OEM <-> Aftermarket Interchange & Core Deposits
# ---------------------------------------------------------------------------

class PartInterchangeGraph:
    """Resolves supersessions (OLD -> NEW OEM) and bidirectional aftermarket equivalents."""

    def __init__(self) -> None:
        self._supersedes: Dict[str, str] = {}
        self._equivalents: Dict[str, Set[str]] = {}

    def register_supersession(self, old_sku: str, new_oem_sku: str) -> None:
        if old_sku == new_oem_sku:
            raise ValueError("Part cannot supersede itself")
        self._supersedes[old_sku] = new_oem_sku

    def register_equivalent(self, sku_a: str, sku_b: str) -> None:
        self._equivalents.setdefault(sku_a, set()).add(sku_b)
        self._equivalents.setdefault(sku_b, set()).add(sku_a)

    def find_compatible_stock(
        self, requested_sku: str, on_hand_by_sku: Dict[str, float | str | Decimal]
    ) -> List[Dict[str, Any]]:
        """Returns compatible in-stock SKUs ordered: EXACT -> SUPERSEDED_OEM -> AFTERMARKET_EQUIV."""
        candidates: List[Tuple[str, str]] = [(requested_sku, "EXACT")]
        seen: Set[str] = {requested_sku}

        # Follow OEM supersession chain
        cur = requested_sku
        while cur in self._supersedes:
            nxt = self._supersedes[cur]
            if nxt in seen:
                break
            seen.add(nxt)
            candidates.append((nxt, "SUPERSEDED_OEM"))
            cur = nxt

        # BFS across aftermarket equivalence graph from all OEM chain nodes
        q = deque(list(seen))
        while q:
            node = q.popleft()
            for eq in sorted(self._equivalents.get(node, ())):
                if eq not in seen:
                    seen.add(eq)
                    candidates.append((eq, "AFTERMARKET_EQUIVALENT"))
                    q.append(eq)

        available = []
        for sku, rel in candidates:
            qty = _d(on_hand_by_sku.get(sku, 0))
            if qty > 0:
                available.append({"sku": sku, "match_type": rel, "available_qty": qty})
        return available


class CoreDepositTracker:
    """Tracks refundable core charges (car batteries, alternators, gas cylinders)."""

    def __init__(self) -> None:
        self._open_deposits: Dict[str, Dict[str, Any]] = {}

    def issue_with_core_charge(
        self, invoice_id: str, sku: str, qty: int, deposit_per_unit: float | str | Decimal
    ) -> Dict[str, Any]:
        if qty <= 0:
            raise ValueError("qty must be > 0")
        dep = _d(deposit_per_unit).quantize(P2, rounding=ROUND_HALF_UP)
        rec = {
            "invoice_id": invoice_id,
            "sku": sku,
            "issued_qty": qty,
            "returned_core_qty": 0,
            "deposit_per_unit": dep,
            "deposit_held": (dep * qty).quantize(P2, rounding=ROUND_HALF_UP),
        }
        self._open_deposits[invoice_id] = rec
        return dict(rec)

    def return_dirty_core(self, invoice_id: str, returned_qty: int) -> Dict[str, Any]:
        if invoice_id not in self._open_deposits:
            raise KeyError(f"Unknown core deposit invoice {invoice_id!r}")
        rec = self._open_deposits[invoice_id]
        remaining = rec["issued_qty"] - rec["returned_core_qty"]
        if returned_qty <= 0 or returned_qty > remaining:
            raise ValueError(f"Invalid returned_qty={returned_qty}; remaining open cores={remaining}")
        rec["returned_core_qty"] += returned_qty
        refund = (rec["deposit_per_unit"] * returned_qty).quantize(P2, rounding=ROUND_HALF_UP)
        rec["deposit_held"] = (rec["deposit_held"] - refund).quantize(P2, rounding=ROUND_HALF_UP)
        return {"invoice_id": invoice_id, "refund_amount": refund, "remaining_open_cores": remaining - returned_qty}


# ---------------------------------------------------------------------------
# 3. Equipment, Scaffolding & Tool Crib Rental Loop (Non-Consumable Assets)
# ---------------------------------------------------------------------------

@dataclass
class RentalAsset:
    asset_tag: str
    sku: str
    daily_rate: Decimal
    security_deposit: Decimal
    maintenance_interval_days: int = 90
    cumulative_rental_days: int = 0
    state: str = "AVAILABLE"  # 'AVAILABLE', 'RENTED_OUT', 'MAINTENANCE_LOCKED', 'RETIRED'
    active_contract_id: Optional[str] = None
    checked_out_at: Optional[datetime] = None
    due_back_at: Optional[datetime] = None


class RentalAssetPool:
    """Manages check-out / check-in rental assets, billing, deposits, and maintenance locks."""

    def __init__(self) -> None:
        self._assets: Dict[str, RentalAsset] = {}

    def register_asset(
        self,
        asset_tag: str,
        sku: str,
        daily_rate: float | str | Decimal,
        security_deposit: float | str | Decimal,
        maintenance_interval_days: int = 90,
    ) -> RentalAsset:
        if asset_tag in self._assets:
            raise ValueError(f"Duplicate asset_tag {asset_tag!r}")
        a = RentalAsset(
            asset_tag=asset_tag,
            sku=sku,
            daily_rate=_d(daily_rate).quantize(P2, rounding=ROUND_HALF_UP),
            security_deposit=_d(security_deposit).quantize(P2, rounding=ROUND_HALF_UP),
            maintenance_interval_days=maintenance_interval_days,
        )
        self._assets[asset_tag] = a
        return a

    def checkout(
        self, asset_tag: str, contract_id: str, checked_out_at: datetime, due_back_at: datetime
    ) -> RentalAsset:
        a = self._assets[asset_tag]
        if a.state != "AVAILABLE":
            raise ValueError(f"Asset {asset_tag!r} cannot be rented in state {a.state!r}")
        if due_back_at <= checked_out_at:
            raise ValueError("due_back_at must be after checked_out_at")
        a.state = "RENTED_OUT"
        a.active_contract_id = contract_id
        a.checked_out_at = checked_out_at
        a.due_back_at = due_back_at
        return a

    def checkin(
        self,
        asset_tag: str,
        returned_at: datetime,
        damage_fee: float | str | Decimal = "0.00",
        late_rate_multiplier: float | str | Decimal = "1.50",
        requires_repair: bool = False,
    ) -> Dict[str, Any]:
        a = self._assets[asset_tag]
        if a.state != "RENTED_OUT" or a.checked_out_at is None or a.due_back_at is None:
            raise ValueError(f"Asset {asset_tag!r} is not currently RENTED_OUT")
        if returned_at < a.checked_out_at:
            raise ValueError("returned_at cannot precede checked_out_at")

        total_seconds = (returned_at - a.checked_out_at).total_seconds()
        billed_days = max(1, int((total_seconds + 86399) // 86400))
        overdue_seconds = max(0.0, (returned_at - a.due_back_at).total_seconds())
        overdue_days = int((overdue_seconds + 86399) // 86400) if overdue_seconds > 0 else 0
        regular_days = max(0, billed_days - overdue_days)

        base_charge = (a.daily_rate * regular_days).quantize(P2, rounding=ROUND_HALF_UP)
        late_charge = (a.daily_rate * _d(late_rate_multiplier) * overdue_days).quantize(P2, rounding=ROUND_HALF_UP)
        dmg = _d(damage_fee).quantize(P2, rounding=ROUND_HALF_UP)
        total_due = base_charge + late_charge + dmg
        deposit_refund = max(Decimal("0.00"), a.security_deposit - dmg)
        balance_payable = total_due - (a.security_deposit - deposit_refund)

        a.cumulative_rental_days += billed_days
        a.active_contract_id = None
        a.checked_out_at = None
        a.due_back_at = None

        if requires_repair or a.cumulative_rental_days >= a.maintenance_interval_days:
            a.state = "MAINTENANCE_LOCKED"
        else:
            a.state = "AVAILABLE"

        return {
            "asset_tag": asset_tag,
            "billed_days": billed_days,
            "overdue_days": overdue_days,
            "base_rental_charge": base_charge,
            "late_penalty_charge": late_charge,
            "damage_fee": dmg,
            "total_charges": total_due,
            "deposit_refund": deposit_refund,
            "net_customer_payable": balance_payable,
            "next_asset_state": a.state,
        }

    def complete_maintenance(self, asset_tag: str) -> RentalAsset:
        a = self._assets[asset_tag]
        a.cumulative_rental_days = 0
        a.state = "AVAILABLE"
        return a


# ---------------------------------------------------------------------------
# 4. Multi-Currency + GST/VAT Tax-Stripped Landed Cost (IAS 2 / Ind AS 2)
# ---------------------------------------------------------------------------

def compute_multicurrency_landed_cost(
    accepted_base_qty: float | str | Decimal,
    foreign_unit_price: float | str | Decimal,
    fx_rate_to_base_currency: float | str | Decimal = "1.0",
    customs_duty_pct: float | str | Decimal = "0.0",
    recoverable_gst_vat_pct: float | str | Decimal = "0.0",
    non_recoverable_tax_base_curr: float | str | Decimal = "0.0",
    freight_insurance_base_curr: float | str | Decimal = "0.0",
) -> Dict[str, Decimal]:
    """
    Per IAS 2 / Ind AS 2:
    - Converts foreign invoice at spot FX.
    - Capitalizes customs duty, freight, insurance, and non-recoverable taxes into inventory cost.
    - EXCLUDES recoverable input GST/VAT from inventory asset valuation (booked separately as Input Tax Credit).
    """
    qty = _d(accepted_base_qty)
    if qty <= 0:
        raise ValueError("accepted_base_qty must be > 0")
    fx = _d(fx_rate_to_base_currency)
    if fx <= 0:
        raise ValueError("fx_rate_to_base_currency must be > 0")

    merchandise_base = (qty * _d(foreign_unit_price) * fx).quantize(P2, rounding=ROUND_HALF_UP)
    customs_duty = (merchandise_base * (_d(customs_duty_pct) / Decimal("100"))).quantize(P2, rounding=ROUND_HALF_UP)
    taxable_import_value = merchandise_base + customs_duty
    recoverable_itc = (
        taxable_import_value * (_d(recoverable_gst_vat_pct) / Decimal("100"))
    ).quantize(P2, rounding=ROUND_HALF_UP)

    capitalized_total = (
        merchandise_base + customs_duty + _d(non_recoverable_tax_base_curr) + _d(freight_insurance_base_curr)
    ).quantize(P2, rounding=ROUND_HALF_UP)
    landed_unit = (capitalized_total / qty).quantize(Q6, rounding=ROUND_HALF_UP)

    return {
        "merchandise_base_currency": merchandise_base,
        "customs_duty_capitalized": customs_duty,
        "recoverable_input_tax_credit": recoverable_itc,
        "capitalized_inventory_value": capitalized_total,
        "landed_unit_cost": landed_unit,
    }


# ---------------------------------------------------------------------------
# 5. Offline Field Sync Queue & Deterministic Conflict Resolver
# ---------------------------------------------------------------------------

def replay_offline_sync_batch(
    ledger: ImmutableStockLedger,
    queued_events: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Replays offline device stock movements in deterministic (client_timestamp, seq) order.
    Valid movements commit cleanly; conflicting/insufficient-stock offline issues are quarantined
    into structured conflict records without aborting non-conflicting offline work.
    """
    ordered = sorted(queued_events, key=lambda e: (str(e.get("client_timestamp", "")), int(e.get("seq", 0))))
    applied: List[int] = []
    conflicts: List[Dict[str, Any]] = []

    for ev in ordered:
        try:
            entry = ledger.post_movement(
                idempotency_key=ev["idempotency_key"],
                sku=ev["sku"],
                lot_id=ev["lot_id"],
                movement_type=ev["movement_type"],
                signed_base_qty=ev["signed_base_qty"],
                reference_doc=ev.get("reference_doc", "OFFLINE_SYNC"),
                actor_id=ev.get("actor_id", "offline-device"),
                inbound_landed_unit_cost=ev.get("inbound_landed_unit_cost"),
                wbs_task_code=ev.get("wbs_task_code"),
            )
            applied.append(entry.ledger_id)
        except ValueError as exc:
            conflicts.append({
                "idempotency_key": ev.get("idempotency_key"),
                "sku": ev.get("sku"),
                "lot_id": ev.get("lot_id"),
                "requested_signed_qty": ev.get("signed_base_qty"),
                "status": "SYNC_CONFLICT_REVIEW",
                "reason": str(exc),
            })

    return {
        "applied_count": len(applied),
        "conflict_count": len(conflicts),
        "applied_ledger_ids": applied,
        "conflicts": conflicts,
    }
