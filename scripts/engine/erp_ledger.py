"""
Enterprise Operational ERP Layer:
1. Multi-UOM Conversion & BOQ Recipe Explosion (Decimal-exact)
2. Weighbridge GRN Receiving, Moisture Deduction, Landed Freight & 3-Way PO Matching
3. Thread-Safe Idempotent Stock Movement Ledger (Strict Movement/Sign/SKU Guards)
4. Moving Weighted Average Cost (MWAC) + Original-Issue Site Return Valuation
5. Atomic Paired Inter-Site Transfers, Blind Cycle Counts & BOQ Wastage Audits
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple


D6 = Decimal("0.000001")
D2 = Decimal("0.01")

POSITIVE_MOVEMENTS = frozenset({"RECEIPT_GRN", "RETURN_FROM_SITE", "TRANSFER_IN"})
NEGATIVE_MOVEMENTS = frozenset({"ISSUE_OUT", "TRANSFER_OUT", "RETURN_TO_VENDOR", "SCRAP_EXPIRED"})
BIDIRECTIONAL_MOVEMENTS = frozenset({"CYCLE_COUNT_ADJUST"})
VALID_MOVEMENT_TYPES = POSITIVE_MOVEMENTS | NEGATIVE_MOVEMENTS | BIDIRECTIONAL_MOVEMENTS


class InvalidMovementError(ValueError):
    """Raised on unknown movement_type or illegal quantity sign."""


class IdempotencyConflictError(ValueError):
    """Raised when an idempotency_key is reused with a different movement payload."""


class LotSKUMismatchError(ValueError):
    """Raised when a lot_id already bound to one SKU is referenced under another SKU."""


def dec(val: float | int | str | Decimal) -> Decimal:
    return val if isinstance(val, Decimal) else Decimal(str(val))


# ---------------------------------------------------------------------------
# 1. Multi-UOM & BOQ Recipe Engine
# ---------------------------------------------------------------------------

class UOMRegistry:
    """Converts purchasing/packaging units cleanly into canonical item base_uom."""

    def __init__(self) -> None:
        self._factors: Dict[str, Dict[str, Decimal]] = {}

    def register(self, sku: str, base_uom: str, conversions: Dict[str, float | str | Decimal]) -> None:
        sku_map = {base_uom.upper(): Decimal("1")}
        for uom, factor in conversions.items():
            f = dec(factor)
            if f <= 0:
                raise ValueError(f"UOM multiplier for {uom} must be > 0")
            sku_map[uom.upper()] = f
        self._factors[sku] = sku_map

    def to_base(self, sku: str, qty: float | str | Decimal, from_uom: str) -> Decimal:
        u = from_uom.upper()
        if sku not in self._factors or u not in self._factors[sku]:
            raise KeyError(f"Unregistered UOM {from_uom!r} for SKU {sku!r}")
        return (dec(qty) * self._factors[sku][u]).quantize(D6, rounding=ROUND_HALF_UP)

    def from_base(self, sku: str, base_qty: float | str | Decimal, to_uom: str) -> Decimal:
        u = to_uom.upper()
        if sku not in self._factors or u not in self._factors[sku]:
            raise KeyError(f"Unregistered UOM {to_uom!r} for SKU {sku!r}")
        return (dec(base_qty) / self._factors[sku][u]).quantize(D6, rounding=ROUND_HALF_UP)


@dataclass
class BOQRecipeLine:
    sku: str
    norm_base_qty_per_unit: Decimal
    allowable_wastage_pct: Decimal


def explode_boq_recipe(
    target_output_units: float | str | Decimal, lines: List[BOQRecipeLine]
) -> List[Dict[str, Decimal | str]]:
    out_qty = dec(target_output_units)
    exploded = []
    for line in lines:
        theoretical = (out_qty * line.norm_base_qty_per_unit).quantize(D6, rounding=ROUND_HALF_UP)
        with_wastage = (
            theoretical * (Decimal("1") + (line.allowable_wastage_pct / Decimal("100")))
        ).quantize(D6, rounding=ROUND_HALF_UP)
        exploded.append({
            "sku": line.sku,
            "theoretical_base_qty": theoretical,
            "allowable_wastage_pct": line.allowable_wastage_pct,
            "requisition_base_qty": with_wastage,
        })
    return exploded


# ---------------------------------------------------------------------------
# 2. Weighbridge GRN, Landed Cost & 3-Way PO Match
# ---------------------------------------------------------------------------

@dataclass
class GRNEvaluationResult:
    grn_number: str
    sku: str
    challan_billed_base_qty: Decimal
    net_dry_weighbridge_qty: Decimal
    qc_rejected_base_qty: Decimal
    accepted_base_qty: Decimal
    shortage_vs_challan_qty: Decimal
    shortage_pct: Decimal
    debit_note_triggered: bool
    vendor_unit_cost: Decimal
    landed_unit_cost: Decimal


def evaluate_weighbridge_grn(
    grn_number: str,
    sku: str,
    challan_billed_base_qty: float | str | Decimal,
    gross_weight_base: float | str | Decimal,
    tare_weight_base: float | str | Decimal,
    measured_moisture_pct: float | str | Decimal = 0,
    standard_moisture_pct: float | str | Decimal = 0,
    qc_rejected_base_qty: float | str | Decimal = 0,
    vendor_unit_cost: float | str | Decimal = 0,
    allocated_freight_cost: float | str | Decimal = 0,
    shortage_tolerance_pct: float | str | Decimal = "0.5",
) -> GRNEvaluationResult:
    challan = dec(challan_billed_base_qty)
    raw_net = dec(gross_weight_base) - dec(tare_weight_base)
    if raw_net < 0:
        raise ValueError("Gross weight cannot be less than tare weight")

    excess_moisture = max(Decimal("0"), dec(measured_moisture_pct) - dec(standard_moisture_pct))
    net_dry = (raw_net * (Decimal("1") - excess_moisture / Decimal("100"))).quantize(D6, rounding=ROUND_HALF_UP)
    rejected = dec(qc_rejected_base_qty)
    accepted = max(Decimal("0"), net_dry - rejected).quantize(D6, rounding=ROUND_HALF_UP)

    shortage = (challan - net_dry).quantize(D6, rounding=ROUND_HALF_UP)
    shortage_pct = (
        ((shortage / challan) * Decimal("100")).quantize(D2, rounding=ROUND_HALF_UP)
        if challan > 0
        else Decimal("0")
    )
    debit_flag = shortage_pct > dec(shortage_tolerance_pct)

    v_cost = dec(vendor_unit_cost)
    freight = dec(allocated_freight_cost)
    landed_unit = (
        (((accepted * v_cost) + freight) / accepted).quantize(D6, rounding=ROUND_HALF_UP)
        if accepted > 0
        else v_cost
    )

    return GRNEvaluationResult(
        grn_number=grn_number,
        sku=sku,
        challan_billed_base_qty=challan,
        net_dry_weighbridge_qty=net_dry,
        qc_rejected_base_qty=rejected,
        accepted_base_qty=accepted,
        shortage_vs_challan_qty=shortage,
        shortage_pct=shortage_pct,
        debit_note_triggered=debit_flag,
        vendor_unit_cost=v_cost,
        landed_unit_cost=landed_unit,
    )


def three_way_match_po(
    po_ordered_qty: float | str | Decimal,
    po_unit_price: float | str | Decimal,
    grn_accepted_qty: float | str | Decimal,
    invoice_billed_qty: float | str | Decimal,
    invoice_unit_price: float | str | Decimal,
    qty_tolerance_pct: float | str | Decimal = "0.5",
    price_tolerance_pct: float | str | Decimal = "0.5",
) -> Dict[str, Any]:
    """Performs PO <-> GRN <-> Vendor Invoice 3-way accounts payable verification."""
    po_q, po_p = dec(po_ordered_qty), dec(po_unit_price)
    grn_q = dec(grn_accepted_qty)
    inv_q, inv_p = dec(invoice_billed_qty), dec(invoice_unit_price)

    payable_qty = min(po_q, grn_q)
    qty_overbill_pct = (
        ((inv_q - payable_qty) / payable_qty * Decimal("100")).quantize(D2, rounding=ROUND_HALF_UP)
        if payable_qty > 0
        else (Decimal("100") if inv_q > 0 else Decimal("0"))
    )
    price_overbill_pct = (
        ((inv_p - po_p) / po_p * Decimal("100")).quantize(D2, rounding=ROUND_HALF_UP)
        if po_p > 0
        else Decimal("0")
    )

    discrepancies: List[str] = []
    if qty_overbill_pct > dec(qty_tolerance_pct):
        discrepancies.append(f"Invoice qty ({inv_q}) exceeds GRN/PO payable qty ({payable_qty}) by {qty_overbill_pct}%")
    if price_overbill_pct > dec(price_tolerance_pct):
        discrepancies.append(f"Invoice price ({inv_p}) exceeds PO price ({po_p}) by {price_overbill_pct}%")

    approved_amount = (min(inv_q, payable_qty) * min(inv_p, po_p)).quantize(D2, rounding=ROUND_HALF_UP)
    return {
        "matched": len(discrepancies) == 0,
        "payable_qty": payable_qty,
        "approved_payment_amount": approved_amount,
        "discrepancies": discrepancies,
    }


# ---------------------------------------------------------------------------
# 3. Immutable Stock Ledger + Concurrency + MWAC + Transfers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LedgerEntry:
    ledger_id: int
    idempotency_key: str
    sku: str
    lot_id: str
    movement_type: str
    signed_base_qty: Decimal
    unit_cost_applied: Decimal
    total_cost_impact: Decimal
    mwac_after: Decimal
    reference_doc: str
    wbs_task_code: Optional[str]
    actor_id: str
    timestamp: str


class ImmutableStockLedger:
    """
    Append-only thread-safe inventory ledger enforcing:
    - Strict movement_type and quantity sign invariants
    - Lot-to-SKU binding (prevents cross-SKU lot corruption)
    - Payload-verified idempotency keys (rejects conflicting reuse)
    - Original-issue unit cost snapshot on RETURN_FROM_SITE
    - Atomic paired TRANSFER_OUT / TRANSFER_IN
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: List[LedgerEntry] = []
        self._idempotency_index: Dict[str, Tuple[Tuple[Any, ...], LedgerEntry]] = {}
        self._lot_balances: Dict[Tuple[str, str], Decimal] = {}
        self._lot_sku_binding: Dict[str, str] = {}
        self._lot_receipt_cost: Dict[Tuple[str, str], Decimal] = {}
        self._sku_mwac: Dict[str, Decimal] = {}

    def sku_total_qty(self, sku: str) -> Decimal:
        with self._lock:
            return sum(
                (qty for (s, _), qty in self._lot_balances.items() if s == sku),
                Decimal("0"),
            )

    def lot_qty(self, sku: str, lot_id: str) -> Decimal:
        with self._lock:
            return self._lot_balances.get((sku, lot_id), Decimal("0"))

    def current_mwac(self, sku: str) -> Decimal:
        with self._lock:
            return self._sku_mwac.get(sku, Decimal("0"))

    def _find_original_issue_cost(
        self,
        sku: str,
        lot_id: str,
        wbs_task_code: Optional[str],
        original_issue_ledger_id: Optional[int],
    ) -> Optional[Decimal]:
        if original_issue_ledger_id is not None:
            for e in self._entries:
                if e.ledger_id == original_issue_ledger_id and e.sku == sku and e.movement_type == "ISSUE_OUT":
                    return e.unit_cost_applied
            raise ValueError(f"Original ISSUE_OUT ledger_id={original_issue_ledger_id} not found for SKU {sku!r}")

        # Prefer matching (sku, lot_id, wbs_task_code), then (sku, lot_id), then sku
        for entry in reversed(self._entries):
            if entry.movement_type == "ISSUE_OUT" and entry.sku == sku and entry.lot_id == lot_id:
                if wbs_task_code is None or entry.wbs_task_code == wbs_task_code:
                    return entry.unit_cost_applied
        for entry in reversed(self._entries):
            if entry.movement_type == "ISSUE_OUT" and entry.sku == sku:
                return entry.unit_cost_applied
        return None

    def post_movement(
        self,
        idempotency_key: str,
        sku: str,
        lot_id: str,
        movement_type: str,
        signed_base_qty: float | str | Decimal,
        reference_doc: str,
        actor_id: str,
        inbound_landed_unit_cost: Optional[float | str | Decimal] = None,
        wbs_task_code: Optional[str] = None,
        original_issue_ledger_id: Optional[int] = None,
    ) -> LedgerEntry:
        with self._lock:
            mtype = movement_type.strip().upper()
            if mtype not in VALID_MOVEMENT_TYPES:
                raise InvalidMovementError(
                    f"Unknown movement_type {movement_type!r}; valid: {sorted(VALID_MOVEMENT_TYPES)}"
                )

            qty_delta = dec(signed_base_qty).quantize(D6, rounding=ROUND_HALF_UP)
            if qty_delta == 0:
                raise InvalidMovementError("Ledger movement quantity cannot be zero")
            if mtype in POSITIVE_MOVEMENTS and qty_delta <= 0:
                raise InvalidMovementError(f"{mtype} requires positive signed_base_qty, got {qty_delta}")
            if mtype in NEGATIVE_MOVEMENTS and qty_delta >= 0:
                raise InvalidMovementError(f"{mtype} requires negative signed_base_qty, got {qty_delta}")

            fingerprint = (sku, lot_id, mtype, qty_delta, reference_doc, wbs_task_code)
            if idempotency_key in self._idempotency_index:
                prev_fp, prev_entry = self._idempotency_index[idempotency_key]
                if prev_fp != fingerprint:
                    raise IdempotencyConflictError(
                        f"Idempotency key {idempotency_key!r} reused with conflicting payload: "
                        f"expected {prev_fp}, got {fingerprint}"
                    )
                return prev_entry

            if lot_id in self._lot_sku_binding and self._lot_sku_binding[lot_id] != sku:
                raise LotSKUMismatchError(
                    f"Lot {lot_id!r} belongs to SKU {self._lot_sku_binding[lot_id]!r}, cannot post SKU {sku!r}"
                )

            old_lot_qty = self._lot_balances.get((sku, lot_id), Decimal("0"))
            new_lot_qty = old_lot_qty + qty_delta
            if new_lot_qty < 0:
                raise ValueError(
                    f"Insufficient stock on lot {lot_id} ({sku}): on_hand={old_lot_qty}, delta={qty_delta}"
                )

            old_sku_qty = self.sku_total_qty(sku)
            old_mwac = self._sku_mwac.get(sku, Decimal("0"))

            if mtype == "RECEIPT_GRN":
                if inbound_landed_unit_cost is None:
                    raise ValueError("inbound_landed_unit_cost required for RECEIPT_GRN")
                unit_cost = dec(inbound_landed_unit_cost).quantize(D6, rounding=ROUND_HALF_UP)
                if unit_cost < 0:
                    raise ValueError("inbound_landed_unit_cost cannot be negative")
                self._lot_receipt_cost[(sku, lot_id)] = unit_cost
                new_mwac = (
                    ((old_sku_qty * old_mwac) + (qty_delta * unit_cost)) / (old_sku_qty + qty_delta)
                ).quantize(D6, rounding=ROUND_HALF_UP)
                self._sku_mwac[sku] = new_mwac

            elif mtype == "RETURN_FROM_SITE":
                if inbound_landed_unit_cost is not None:
                    unit_cost = dec(inbound_landed_unit_cost).quantize(D6, rounding=ROUND_HALF_UP)
                else:
                    orig_cost = self._find_original_issue_cost(sku, lot_id, wbs_task_code, original_issue_ledger_id)
                    unit_cost = orig_cost if orig_cost is not None else old_mwac
                new_mwac = (
                    ((old_sku_qty * old_mwac) + (qty_delta * unit_cost)) / (old_sku_qty + qty_delta)
                ).quantize(D6, rounding=ROUND_HALF_UP)
                self._sku_mwac[sku] = new_mwac

            elif mtype == "RETURN_TO_VENDOR":
                unit_cost = (
                    dec(inbound_landed_unit_cost).quantize(D6, rounding=ROUND_HALF_UP)
                    if inbound_landed_unit_cost is not None
                    else self._lot_receipt_cost.get((sku, lot_id), old_mwac)
                )
                rem_qty = old_sku_qty + qty_delta  # qty_delta is negative
                if rem_qty > 0:
                    new_val = max(Decimal("0"), (old_sku_qty * old_mwac) + (qty_delta * unit_cost))
                    new_mwac = (new_val / rem_qty).quantize(D6, rounding=ROUND_HALF_UP)
                else:
                    new_mwac = Decimal("0")
                self._sku_mwac[sku] = new_mwac

            else:
                unit_cost = (
                    dec(inbound_landed_unit_cost).quantize(D6, rounding=ROUND_HALF_UP)
                    if inbound_landed_unit_cost is not None
                    else old_mwac
                )
                new_mwac = old_mwac

            self._lot_sku_binding[lot_id] = sku
            self._lot_balances[(sku, lot_id)] = new_lot_qty
            total_impact = (qty_delta * unit_cost).quantize(D2, rounding=ROUND_HALF_UP)

            entry = LedgerEntry(
                ledger_id=len(self._entries) + 1,
                idempotency_key=idempotency_key,
                sku=sku,
                lot_id=lot_id,
                movement_type=mtype,
                signed_base_qty=qty_delta,
                unit_cost_applied=unit_cost,
                total_cost_impact=total_impact,
                mwac_after=new_mwac,
                reference_doc=reference_doc,
                wbs_task_code=wbs_task_code,
                actor_id=actor_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self._entries.append(entry)
            self._idempotency_index[idempotency_key] = (fingerprint, entry)
            return entry

    def transfer_stock(
        self,
        idempotency_prefix: str,
        sku: str,
        from_lot_id: str,
        to_lot_id: str,
        qty: float | str | Decimal,
        reference_doc: str,
        actor_id: str,
    ) -> Tuple[LedgerEntry, LedgerEntry]:
        """Atomically transfers stock out of from_lot_id and into to_lot_id at identical MWAC."""
        with self._lock:
            q = dec(qty).quantize(D6, rounding=ROUND_HALF_UP)
            if q <= 0:
                raise InvalidMovementError(f"Transfer quantity must be > 0, got {q}")
            if from_lot_id == to_lot_id:
                raise InvalidMovementError("Source and destination lot_id must differ")

            cost = self.current_mwac(sku)
            out_entry = self.post_movement(
                idempotency_key=f"{idempotency_prefix}:OUT",
                sku=sku,
                lot_id=from_lot_id,
                movement_type="TRANSFER_OUT",
                signed_base_qty=-q,
                inbound_landed_unit_cost=cost,
                reference_doc=reference_doc,
                actor_id=actor_id,
            )
            in_entry = self.post_movement(
                idempotency_key=f"{idempotency_prefix}:IN",
                sku=sku,
                lot_id=to_lot_id,
                movement_type="TRANSFER_IN",
                signed_base_qty=q,
                inbound_landed_unit_cost=cost,
                reference_doc=reference_doc,
                actor_id=actor_id,
            )
            return out_entry, in_entry

    @property
    def entries(self) -> List[LedgerEntry]:
        return list(self._entries)


def transfer_stock(
    ledger: ImmutableStockLedger,
    idempotency_prefix: str,
    sku: str,
    from_lot_id: str,
    to_lot_id: str,
    qty: float | str | Decimal,
    reference_doc: str = "STOCK_TRANSFER",
    actor_id: str = "system",
) -> Tuple[LedgerEntry, LedgerEntry]:
    return ledger.transfer_stock(idempotency_prefix, sku, from_lot_id, to_lot_id, qty, reference_doc, actor_id)


post_transfer = transfer_stock


# ---------------------------------------------------------------------------
# 4. Blind Cycle Count & Construction BOQ Wastage Reconciliation
# ---------------------------------------------------------------------------

def reconcile_blind_cycle_count(
    ledger: ImmutableStockLedger,
    idempotency_key: str,
    sku: str,
    lot_id: str,
    blind_counted_base_qty: float | str | Decimal,
    audit_doc_id: str,
    actor_id: str,
) -> Dict[str, Any]:
    book_qty = ledger.lot_qty(sku, lot_id)
    counted = dec(blind_counted_base_qty).quantize(D6, rounding=ROUND_HALF_UP)
    if counted < 0:
        raise ValueError("blind_counted_base_qty cannot be negative")
    variance = counted - book_qty
    entry = None
    if variance != 0:
        entry = ledger.post_movement(
            idempotency_key=idempotency_key,
            sku=sku,
            lot_id=lot_id,
            movement_type="CYCLE_COUNT_ADJUST",
            signed_base_qty=variance,
            reference_doc=audit_doc_id,
            actor_id=actor_id,
        )
    return {
        "sku": sku,
        "lot_id": lot_id,
        "system_book_qty": book_qty,
        "blind_counted_base_qty": counted,
        "variance_qty": variance,
        "cost_impact": entry.total_cost_impact if entry else Decimal("0.00"),
        "adjustment_posted": entry is not None,
    }


def audit_boq_wastage(
    ledger: ImmutableStockLedger,
    wbs_task_code: str,
    sku: str,
    theoretical_boq_base_qty: float | str | Decimal,
    allowable_wastage_pct: float | str | Decimal,
) -> Dict[str, Any]:
    theo = dec(theoretical_boq_base_qty)
    if theo <= 0:
        raise ValueError("theoretical_boq_base_qty must be > 0")
    allow_pct = dec(allowable_wastage_pct)
    max_allowed = (theo * (Decimal("1") + allow_pct / Decimal("100"))).quantize(D6, rounding=ROUND_HALF_UP)

    net_issued = Decimal("0")
    for e in ledger.entries:
        if e.sku == sku and e.wbs_task_code == wbs_task_code:
            if e.movement_type == "ISSUE_OUT":
                net_issued += abs(e.signed_base_qty)
            elif e.movement_type == "RETURN_FROM_SITE":
                net_issued -= e.signed_base_qty

    excess_qty = net_issued - max_allowed
    excess_pct = ((excess_qty / theo) * Decimal("100")).quantize(D2, rounding=ROUND_HALF_UP)
    return {
        "wbs_task_code": wbs_task_code,
        "sku": sku,
        "theoretical_boq_qty": theo,
        "allowable_wastage_pct": allow_pct,
        "max_allowed_qty": max_allowed,
        "actual_net_issued_qty": net_issued,
        "excess_wastage_qty": excess_qty,
        "excess_wastage_pct": excess_pct,
        "audit_flag_triggered": excess_qty > 0,
    }
