"""
Pharmacy, Hospital & Medical Device Regulatory Compliance Engine:
1. GS1 DataMatrix Barcode Parser: AI (01) GTIN-14, (17) Expiry w/ DD=00 rule, (10) Batch, (21) Serial
2. Schedule H / H1 / Narcotics (Schedule X) Perpetual Register with Dual-Witness & SHA-256 Hash Chain
3. Multi-Location Atomic Batch Recall Freeze & Dispensed-Patient Traceability
4. Opened Multi-Dose Vial / Strip Beyond-Use-Date (BUD) Shortening
5. Automated Cold-Chain IoT Excursion & MKT Quarantine Evaluator
6. Near-Expiry Distributor Return & Credit Claim Builder
"""

from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Set

from .core import StockLot, TemperatureLog, arrhenius_mkt_celsius


Q6 = Decimal("0.000001")
P2 = Decimal("0.01")

DRUG_SCHEDULES = frozenset({"OTC", "SCHEDULE_H", "SCHEDULE_H1", "NARCOTIC_SCHEDULE_X"})
RX_REQUIRED_SCHEDULES = frozenset({"SCHEDULE_H", "SCHEDULE_H1", "NARCOTIC_SCHEDULE_X"})
DUAL_WITNESS_SCHEDULES = frozenset({"NARCOTIC_SCHEDULE_X"})


class PharmacyComplianceError(ValueError):
    """Raised when a pharmacy regulatory guardrail (Rx, witness, recall, cold-chain) is violated."""


# ---------------------------------------------------------------------------
# 1. GS1 DataMatrix Parser: (01) GTIN-14, (17) YYMMDD, (10) Batch, (21) Serial
# ---------------------------------------------------------------------------

def gtin14_check_digit(first_thirteen: str) -> str:
    if len(first_thirteen) != 13 or not first_thirteen.isdigit():
        raise ValueError(f"Expected 13 digits for GTIN-14 check calculation, got {first_thirteen!r}")
    # GS1 standard Modulo-10 weighting from right to left (3, 1, 3, 1...)
    total = sum((int(ch) * (3 if idx % 2 == 0 else 1)) for idx, ch in enumerate(first_thirteen))
    return str((10 - (total % 10)) % 10)


def _parse_gs1_yymmdd(yymmdd: str) -> date:
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        raise ValueError(f"Invalid GS1 YYMMDD expiry {yymmdd!r}")
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    year = 2000 + yy if yy < 80 else 1900 + yy
    if mm < 1 or mm > 12:
        raise ValueError(f"Invalid month in GS1 expiry {yymmdd!r}")
    # Per GS1 General Specifications: DD == 00 means last day of that calendar month
    if dd == 0:
        dd = calendar.monthrange(year, mm)[1]
    return date(year, mm, dd)


def parse_gs1_datamatrix(raw_scan: str, verify_gtin_checksum: bool = True) -> Dict[str, Any]:
    """
    Parses GS1 Application Identifier strings in either bracketed HRI format
    '(01)08901234567891(17)270200(10)LOT99(21)SER123' or raw GS/FNC1 (\x1d) format.
    """
    s = raw_scan.strip()
    if s.startswith("]d2") or s.startswith("]C1"):
        s = s[3:]

    extracted: Dict[str, str] = {}
    if "(" in s and ")" in s:
        tokens = re.findall(r"\((\d{2,4})\)\s*([^()]+)", s)
        for ai, val in tokens:
            extracted[ai] = val.strip()
    else:
        pos = 0
        while pos < len(s):
            if s[pos] == "\x1d":
                pos += 1
                continue
            ai2 = s[pos : pos + 2]
            if ai2 == "01":
                extracted["01"] = s[pos + 2 : pos + 16]
                pos += 16
            elif ai2 == "17":
                extracted["17"] = s[pos + 2 : pos + 8]
                pos += 8
            elif ai2 in ("10", "21"):
                end = s.find("\x1d", pos + 2)
                if end == -1:
                    extracted[ai2] = s[pos + 2 :]
                    break
                extracted[ai2] = s[pos + 2 : end]
                pos = end + 1
            else:
                raise ValueError(f"Unsupported unbracketed GS1 AI starting at {s[pos:]!r}")

    gtin = extracted.get("01")
    if not gtin or len(gtin) != 14 or not gtin.isdigit():
        raise ValueError(f"Missing or invalid 14-digit GS1 AI (01) GTIN in {raw_scan!r}")
    if verify_gtin_checksum and gtin[13] != gtin14_check_digit(gtin[:13]):
        raise ValueError(f"GTIN-14 check digit failed for {gtin!r}")

    expiry = _parse_gs1_yymmdd(extracted["17"]) if "17" in extracted else None
    return {
        "gtin14": gtin,
        "expiry_date": expiry,
        "batch_number": extracted.get("10"),
        "serial_number": extracted.get("21"),
    }


# ---------------------------------------------------------------------------
# 2. Controlled Substance / Schedule H1 / Narcotics Register + Recall Freeze
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ControlledRegisterEntry:
    seq_no: int
    sku: str
    schedule: str
    batch_number: str
    movement_type: str      # 'RECEIPT', 'DISPENSE', 'WASTAGE_DESTRUCTION', 'RETURN'
    signed_qty: Decimal
    running_balance: Decimal
    actor_id: str
    witness_id: Optional[str]
    patient_id: Optional[str]
    prescription_id: Optional[str]
    prescriber_reg_no: Optional[str]
    location_id: str
    timestamp: str
    prev_hash: str
    entry_hash: str


class ControlledSubstanceRegister:
    """
    Tamper-evident perpetual register for Schedule H / H1 / X (Narcotics),
    with prescription linkage, mandatory dual-witness checks, and batch recall freeze.
    """

    def __init__(self) -> None:
        self._entries: List[ControlledRegisterEntry] = []
        self._balances: Dict[str, Decimal] = {}
        self._recalled_batches: Dict[Tuple[str, str], str] = {}

    def trigger_batch_recall(
        self, sku: str, batch_number: str, reason: str, lots: Optional[Sequence[StockLot]] = None
    ) -> Dict[str, Any]:
        """Atomically blocks a recalled (sku, batch_number) everywhere and traces affected patients."""
        key = (sku, batch_number)
        self._recalled_batches[key] = reason

        quarantined_lot_ids: List[str] = []
        quarantined_qty = Decimal("0")
        for lot in (lots or []):
            if lot.item.sku == sku and lot.batch_number == batch_number:
                quarantined_lot_ids.append(lot.lot_id)
                quarantined_qty += lot.available_quantity

        affected_dispenses = [
            {
                "seq_no": e.seq_no,
                "patient_id": e.patient_id,
                "prescription_id": e.prescription_id,
                "dispensed_qty": abs(e.signed_qty),
                "location_id": e.location_id,
                "timestamp": e.timestamp,
            }
            for e in self._entries
            if e.sku == sku and e.batch_number == batch_number and e.movement_type == "DISPENSE"
        ]
        return {
            "sku": sku,
            "batch_number": batch_number,
            "recall_reason": reason,
            "quarantined_lot_ids": quarantined_lot_ids,
            "quarantined_available_qty": quarantined_qty,
            "affected_patient_dispenses": affected_dispenses,
        }

    def is_recalled(self, sku: str, batch_number: str) -> bool:
        return (sku, batch_number) in self._recalled_batches

    def record_event(
        self,
        sku: str,
        schedule: str,
        batch_number: str,
        movement_type: str,
        signed_qty: float | str | Decimal,
        actor_id: str,
        location_id: str,
        witness_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        prescription_id: Optional[str] = None,
        prescriber_reg_no: Optional[str] = None,
    ) -> ControlledRegisterEntry:
        sched = schedule.strip().upper()
        if sched not in DRUG_SCHEDULES:
            raise PharmacyComplianceError(f"Unknown drug schedule {schedule!r}")
        mtype = movement_type.strip().upper()
        if mtype not in {"RECEIPT", "DISPENSE", "WASTAGE_DESTRUCTION", "RETURN"}:
            raise PharmacyComplianceError(f"Unsupported register movement_type {movement_type!r}")

        qty = (signed_qty if isinstance(signed_qty, Decimal) else Decimal(str(signed_qty))).quantize(Q6)
        if mtype in ("RECEIPT", "RETURN") and qty <= 0:
            raise PharmacyComplianceError(f"{mtype} requires positive signed_qty")
        if mtype in ("DISPENSE", "WASTAGE_DESTRUCTION") and qty >= 0:
            raise PharmacyComplianceError(f"{mtype} requires negative signed_qty")

        if mtype == "DISPENSE" and self.is_recalled(sku, batch_number):
            raise PharmacyComplianceError(
                f"RECALL BLOCK: Batch {batch_number!r} of {sku!r} is under active recall "
                f"({self._recalled_batches[(sku, batch_number)]})"
            )

        if mtype == "DISPENSE" and sched in RX_REQUIRED_SCHEDULES:
            if not (patient_id and prescription_id and prescriber_reg_no):
                raise PharmacyComplianceError(
                    f"{sched} dispense requires patient_id, prescription_id, and prescriber_reg_no"
                )

        if sched in DUAL_WITNESS_SCHEDULES and mtype in ("DISPENSE", "WASTAGE_DESTRUCTION"):
            if not witness_id or witness_id.strip() == actor_id.strip():
                raise PharmacyComplianceError(
                    f"{sched} {mtype} requires a distinct second-person witness_id"
                )

        cur_bal = self._balances.get(sku, Decimal("0"))
        new_bal = cur_bal + qty
        if new_bal < 0:
            raise PharmacyComplianceError(f"Negative controlled register balance prohibited for {sku!r}")

        prev_hash = self._entries[-1].entry_hash if self._entries else ("0" * 64)
        ts = datetime.now(timezone.utc).isoformat()
        seq = len(self._entries) + 1
        payload = (
            f"{seq}|{sku}|{sched}|{batch_number}|{mtype}|{qty}|{new_bal}|"
            f"{actor_id}|{witness_id}|{patient_id}|{prescription_id}|{prescriber_reg_no}|{location_id}|{ts}|{prev_hash}"
        )
        entry_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        entry = ControlledRegisterEntry(
            seq_no=seq,
            sku=sku,
            schedule=sched,
            batch_number=batch_number,
            movement_type=mtype,
            signed_qty=qty,
            running_balance=new_bal,
            actor_id=actor_id,
            witness_id=witness_id,
            patient_id=patient_id,
            prescription_id=prescription_id,
            prescriber_reg_no=prescriber_reg_no,
            location_id=location_id,
            timestamp=ts,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self._balances[sku] = new_bal
        self._entries.append(entry)
        return entry

    def verify_chain_integrity(self) -> bool:
        expected_prev = "0" * 64
        for e in self._entries:
            if e.prev_hash != expected_prev:
                return False
            payload = (
                f"{e.seq_no}|{e.sku}|{e.schedule}|{e.batch_number}|{e.movement_type}|{e.signed_qty}|{e.running_balance}|"
                f"{e.actor_id}|{e.witness_id}|{e.patient_id}|{e.prescription_id}|{e.prescriber_reg_no}|{e.location_id}|{e.timestamp}|{e.prev_hash}"
            )
            if hashlib.sha256(payload.encode("utf-8")).hexdigest() != e.entry_hash:
                return False
            expected_prev = e.entry_hash
        return True

    @property
    def entries(self) -> List[ControlledRegisterEntry]:
        return list(self._entries)


# ---------------------------------------------------------------------------
# 3. Open-Vial Beyond-Use-Date (BUD), Cold-Chain Auto-Quarantine & Expiry Claims
# ---------------------------------------------------------------------------

def compute_open_container_bud(
    manufacturer_expiry: datetime,
    opened_at: datetime,
    in_use_stability_hours: int,
) -> datetime:
    """Shortens effective expiry once a multi-dose vial/bottle is punctured or unsealed."""
    if in_use_stability_hours <= 0:
        raise ValueError("in_use_stability_hours must be > 0")
    return min(manufacturer_expiry, opened_at + timedelta(hours=in_use_stability_hours))


def evaluate_cold_chain_excursion(
    logs: Sequence[TemperatureLog],
    min_allowed_c: float = 2.0,
    max_allowed_c: float = 8.0,
    max_mkt_c: float = 8.0,
    freeze_sensitive: bool = True,
) -> Dict[str, Any]:
    """Evaluates cold-chain sensor logs and triggers automatic quarantine on freeze or MKT breach."""
    if not logs:
        return {"quarantine_triggered": False, "mkt_celsius": None, "reasons": []}

    mkt = round(arrhenius_mkt_celsius(list(logs)), 2)
    min_seen = min(l.celsius for l in logs)
    max_seen = max(l.celsius for l in logs)
    reasons: List[str] = []

    if freeze_sensitive and min_seen <= 0.0:
        reasons.append(f"Freeze excursion detected ({min_seen:.1f}°C <= 0.0°C)")
    if min_seen < min_allowed_c and not (freeze_sensitive and min_seen <= 0.0):
        reasons.append(f"Below min temperature band ({min_seen:.1f}°C < {min_allowed_c:.1f}°C)")
    if mkt > max_mkt_c:
        reasons.append(f"Mean Kinetic Temperature breach (MKT {mkt:.2f}°C > {max_mkt_c:.1f}°C)")
    elif max_seen > max_allowed_c + 4.0:
        reasons.append(f"Severe peak heat spike ({max_seen:.1f}°C)")

    return {
        "quarantine_triggered": len(reasons) > 0,
        "mkt_celsius": mkt,
        "min_celsius": min_seen,
        "max_celsius": max_seen,
        "recommended_state": "QUARANTINED" if reasons else "NORMAL_AVAILABLE",
        "reasons": reasons,
    }


def build_near_expiry_return_claims(
    lots: Sequence[StockLot],
    current_date: date,
    return_window_days: int = 90,
    distributor_cutoff_days: int = 15,
    credit_recovery_rate: float | str | Decimal = "0.85",
) -> List[Dict[str, Any]]:
    """Identifies lots eligible for return-to-distributor credit before non-returnable cutoff."""
    rate = Decimal(str(credit_recovery_rate))
    claims: List[Dict[str, Any]] = []
    for lot in lots:
        dte = lot.days_to_expiry(current_date)
        if dte is None or lot.available_quantity <= 0:
            continue
        if distributor_cutoff_days <= dte <= return_window_days:
            expected_credit = (lot.available_quantity * lot.item.base_cost * rate).quantize(P2, rounding=ROUND_HALF_UP)
            claims.append({
                "lot_id": lot.lot_id,
                "sku": lot.item.sku,
                "batch_number": lot.batch_number,
                "days_to_expiry": dte,
                "returnable_qty": lot.available_quantity,
                "expected_credit_amount": expected_credit,
            })
    return claims
