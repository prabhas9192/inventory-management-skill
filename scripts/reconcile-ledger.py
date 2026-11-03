#!/usr/bin/env python3
"""
Standalone CLI: Enterprise ERP Ledger, Multi-UOM, Weighbridge GRN, Cycle Count & BOQ Wastage Verifier
Usage:
  python3 reconcile-ledger.py [--json]
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import (  # noqa: E402
    BOQRecipeLine,
    ImmutableStockLedger,
    UOMRegistry,
    audit_boq_wastage,
    dec,
    evaluate_weighbridge_grn,
    explode_boq_recipe,
    reconcile_blind_cycle_count,
)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Multi-UOM, Weighbridge GRN, MWAC Ledger, Cycle Count & Wastage.")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    args = parser.parse_args()

    # 1. Multi-UOM Setup (Canonical Base UOM = KG)
    uom = UOMRegistry()
    uom.register("CEMT-OPC-53", "KG", {"BAG_50KG": 50, "MT": 1000, "PALLET": 2000})
    uom.register("AGGR-SAND-RIVER", "KG", {"MT": 1000})

    # Convert 20 MT order -> 20,000 KG -> 400 Bags
    ordered_cement_kg = uom.to_base("CEMT-OPC-53", 20, "MT")
    ordered_cement_bags = uom.from_base("CEMT-OPC-53", ordered_cement_kg, "BAG_50KG")

    # Explode M25 Concrete Recipe for a 50 m³ 3rd-Floor Slab Pour
    slab_recipe = explode_boq_recipe(
        target_output_units=50,
        lines=[
            BOQRecipeLine("CEMT-OPC-53", dec("380"), dec("2.0")),      # 380 kg/m³ + 2% wastage
            BOQRecipeLine("AGGR-SAND-RIVER", dec("680"), dec("4.0")),  # 680 kg/m³ + 4% wastage
        ],
    )

    # 2. Weighbridge GRN #1 & #2 (Demonstrating Moisture Deduction, QC Reject & Landed Freight)
    grn_cement_1 = evaluate_weighbridge_grn(
        grn_number="GRN-2026-001",
        sku="CEMT-OPC-53",
        challan_billed_base_qty=ordered_cement_kg,
        gross_weight_base=28400,
        tare_weight_base=8500,          # Raw net = 19,900 kg (100 kg short -> 0.50% border)
        qc_rejected_base_qty=150,       # 3 burst bags (150 kg) rejected at gate
        vendor_unit_cost="0.140000",    # $0.14/kg ($7.00 per 50kg bag)
        allocated_freight_cost="197.50",
        shortage_tolerance_pct="0.4",   # Triggers vendor short-supply debit note!
    )

    grn_cement_2 = evaluate_weighbridge_grn(
        grn_number="GRN-2026-002",
        sku="CEMT-OPC-53",
        challan_billed_base_qty=10000,
        gross_weight_base=18200,
        tare_weight_base=8200,
        qc_rejected_base_qty=0,
        vendor_unit_cost="0.160000",    # Price spike batch: $0.16/kg ($8.00/bag)
        allocated_freight_cost="150.00",
    )

    grn_sand_wet = evaluate_weighbridge_grn(
        grn_number="GRN-2026-003",
        sku="AGGR-SAND-RIVER",
        challan_billed_base_qty=40000,  # Billed 40 MT
        gross_weight_base=52000,
        tare_weight_base=12000,         # 40,000 kg wet weight on scale
        measured_moisture_pct="6.5",    # Wet river sand!
        standard_moisture_pct="2.0",    # Deducts 4.5% excess water weight -> 38,200 kg dry sand
        vendor_unit_cost="0.030000",
        allocated_freight_cost="120.00",
        shortage_tolerance_pct="1.5",
    )

    # 3. Post Receipts & Issues to Immutable Stock Ledger (with Idempotency Test)
    ledger = ImmutableStockLedger()
    ledger.post_movement(
        idempotency_key="grn-001-line-1",
        sku="CEMT-OPC-53",
        lot_id="LOT-C1",
        movement_type="RECEIPT_GRN",
        signed_base_qty=grn_cement_1.accepted_base_qty,
        inbound_landed_unit_cost=grn_cement_1.landed_unit_cost,
        reference_doc="GRN-2026-001",
        actor_id="storekeeper-01",
    )
    ledger.post_movement(
        idempotency_key="grn-002-line-1",
        sku="CEMT-OPC-53",
        lot_id="LOT-C2",
        movement_type="RECEIPT_GRN",
        signed_base_qty=grn_cement_2.accepted_base_qty,
        inbound_landed_unit_cost=grn_cement_2.landed_unit_cost,
        reference_doc="GRN-2026-002",
        actor_id="storekeeper-01",
    )
    ledger.post_movement(
        idempotency_key="grn-003-sand",
        sku="AGGR-SAND-RIVER",
        lot_id="LOT-S1",
        movement_type="RECEIPT_GRN",
        signed_base_qty=grn_sand_wet.accepted_base_qty,
        inbound_landed_unit_cost=grn_sand_wet.landed_unit_cost,
        reference_doc="GRN-2026-003",
        actor_id="storekeeper-01",
    )

    # Issue Cement to 3rd-Floor Slab (50 m³ Theoretical = 19,000 kg; Allowed w/ 2% = 19,380 kg; Actual Issued = 19,750 kg -> Overrun!)
    issue_1 = ledger.post_movement(
        idempotency_key="sis-slab3-c1",
        sku="CEMT-OPC-53",
        lot_id="LOT-C1",
        movement_type="ISSUE_OUT",
        signed_base_qty=-19750,
        reference_doc="SIS-2026-104",
        wbs_task_code="03.30.SLAB-L3",
        actor_id="site-eng-02",
    )
    # Simulate flaky mobile Wi-Fi retry with SAME idempotency_key -> Must NOT double-deduct!
    retry_issue_1 = ledger.post_movement(
        idempotency_key="sis-slab3-c1",
        sku="CEMT-OPC-53",
        lot_id="LOT-C1",
        movement_type="ISSUE_OUT",
        signed_base_qty=-19750,
        reference_doc="SIS-2026-104",
        wbs_task_code="03.30.SLAB-L3",
        actor_id="site-eng-02",
    )
    idempotency_verified = (issue_1.ledger_id == retry_issue_1.ledger_id) and (len(ledger.entries) == 4)

    # 4. Blind Cycle Count & BOQ Wastage Audit
    cycle_audit = reconcile_blind_cycle_count(
        ledger=ledger,
        idempotency_key="audit-2026-nov-c2",
        sku="CEMT-OPC-53",
        lot_id="LOT-C2",
        blind_counted_base_qty=9950,    # 1 bag (50 kg) missing during blind count!
        audit_doc_id="CC-NOV-01",
        actor_id="auditor-01",
    )

    wastage_audit = audit_boq_wastage(
        ledger=ledger,
        wbs_task_code="03.30.SLAB-L3",
        sku="CEMT-OPC-53",
        theoretical_boq_base_qty=19000,
        allowable_wastage_pct="2.0",
    )

    summary = {
        "uom_conversion": {
            "ordered_mt": 20,
            "canonical_kg": ordered_cement_kg,
            "stocking_bags_50kg": ordered_cement_bags,
        },
        "boq_50m3_slab_explosion": slab_recipe,
        "grn_evaluations": [grn_cement_1.__dict__, grn_sand_wet.__dict__],
        "mwac_cement_per_kg": ledger.current_mwac("CEMT-OPC-53"),
        "mwac_cement_per_50kg_bag": (ledger.current_mwac("CEMT-OPC-53") * Decimal("50")).quantize(Decimal("0.01")),
        "idempotency_replay_blocked": idempotency_verified,
        "blind_cycle_count": cycle_audit,
        "boq_wastage_audit": wastage_audit,
        "final_cement_on_hand_kg": ledger.sku_total_qty("CEMT-OPC-53"),
    }

    if args.json:
        print(json.dumps(summary, cls=DecimalEncoder, indent=2))
        return 0

    print("=" * 90)
    print(" ENTERPRISE ERP LEDGER, UOM, GRN, MWAC & WASTAGE RECONCILIATION VERIFICATION")
    print("=" * 90)
    print(f"1. Multi-UOM Conversion : 20 MT Cement -> {ordered_cement_kg:,.0f} KG -> {ordered_cement_bags:,.0f} Bags (50kg)")
    print(f"2. Weighbridge GRN-001  : Billed 20,000 kg | Scale 19,900 kg | QC Reject 150 kg -> Accepted {grn_cement_1.accepted_base_qty:,.0f} kg")
    print(f"   Short-Supply Flag    : Debit Note Triggered = {grn_cement_1.debit_note_triggered} | Landed Unit Cost = ${grn_cement_1.landed_unit_cost}/kg")
    print(f"3. Wet Sand GRN-003     : Scale 40,000 kg @ 6.5% moisture (4.5% excess) -> Dry Accepted = {grn_sand_wet.accepted_base_qty:,.0f} kg")
    print(f"4. Moving Weighted Avg  : Blended Cement MWAC = ${summary['mwac_cement_per_kg']}/kg (${summary['mwac_cement_per_50kg_bag']}/bag)")
    print(f"5. Idempotency Guard    : Duplicate offline mobile retry blocked = {idempotency_verified} (Total ledger entries = {len(ledger.entries)})")
    print(f"6. Blind Cycle Count    : LOT-C2 Book 10,000 kg vs Blind Count 9,950 kg -> Variance {cycle_audit['variance_qty']:,.0f} kg (${cycle_audit['cost_impact']})")
    print(
        f"7. BOQ Wastage Audit    : Task {wastage_audit['wbs_task_code']} | Max Allowed {wastage_audit['max_allowed_qty']:,.0f} kg "
        f"vs Actual {wastage_audit['actual_net_issued_qty']:,.0f} kg -> Excess +{wastage_audit['excess_wastage_pct']}% "
        f"(Alert = {wastage_audit['audit_flag_triggered']})"
    )
    print("=" * 90)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
