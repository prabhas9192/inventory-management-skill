"""
Comprehensive Automated Test Suite for the `inventory-management` Skill.
Run from the repository root via:
  python3 -B -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from engine import (  # noqa: E402
    IdempotencyConflictError,
    ImmutableStockLedger,
    InvalidMovementError,
    InventoryItem,
    LotSKUMismatchError,
    LotStatus,
    MixedSKUOrderError,
    ReservationStateError,
    StockLot,
    TemporalArchetype,
    UOMRegistry,
    abc_xyz_classify,
    audit_boq_wastage,
    cancel_order,
    commit_allocation,
    eoq,
    eoq_maturing,
    eoq_perishable,
    evaluate_lot,
    evaluate_weighbridge_grn,
    expire_stale_reservations,
    fulfill_multi_sku_order,
    fulfill_order,
    post_transfer,
    reconcile_blind_cycle_count,
    release_allocation,
    reorder_point,
    safety_stock,
    three_way_match_po,
    transfer_stock,
)


class TestTemporalAndDispatchEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 11, 3)
        self.milk = InventoryItem(
            "MILK", "Milk", TemporalArchetype.TYPE_1_RAPID_SPOIL,
            "1.50", "3.00", shelf_life_days=7, warning_threshold_days=2,
        )
        self.whisky = InventoryItem(
            "WHISKY", "Single Malt", TemporalArchetype.TYPE_3_MATURING,
            "10.00", "40.00", maturation_days=3 * 365, appreciation_multiplier=3.0,
            maturation_curve="LOGARITHMIC",
        )
        self.wine = InventoryItem(
            "WINE", "Cabernet", TemporalArchetype.TYPE_4_BELL_CURVE,
            "15.00", "35.00", maturation_days=730, peak_quality_days=1825, appreciation_multiplier=2.5,
        )

    def test_expired_lot_excluded_from_dispatch(self) -> None:
        expired = StockLot("E1", self.milk, "B1", 10, date(2026, 10, 20), "Cooler")
        res = fulfill_order([expired], 5, self.today)
        self.assertEqual(res["fulfilled_quantity"], Decimal("0"))
        self.assertEqual(res["unfulfilled_quantity"], Decimal("5"))

    def test_shortfall_and_empty_lots(self) -> None:
        fresh = StockLot("F1", self.milk, "B2", 10, date(2026, 11, 1), "Cooler")
        res = fulfill_order([fresh], 50, self.today)
        self.assertEqual(res["fulfilled_quantity"], Decimal("10"))
        self.assertEqual(res["unfulfilled_quantity"], Decimal("40"))
        empty_res = fulfill_order([], 5, self.today)
        self.assertEqual(empty_res["fulfilled_quantity"], Decimal("0"))

    def test_mixed_sku_rejected_and_multi_sku_supported(self) -> None:
        lot_m = StockLot("L-M", self.milk, "BM", 10, date(2026, 11, 1), "C1")
        lot_w = StockLot("L-W", self.whisky, "BW", 10, date(2020, 1, 1), "C2")
        with self.assertRaises(MixedSKUOrderError):
            fulfill_order([lot_m, lot_w], 15, self.today)

        multi = fulfill_multi_sku_order([lot_m, lot_w], {"MILK": 4, "WHISKY": 3}, self.today)
        self.assertEqual(multi["MILK"]["fulfilled_quantity"], Decimal("4"))
        self.assertEqual(multi["WHISKY"]["fulfilled_quantity"], Decimal("3"))
        self.assertIn("FMFO", multi["WHISKY"]["strategy"])

    def test_cement_quality_independent_of_sku_prefix(self) -> None:
        c1 = InventoryItem(
            "CEMT-X", "Cement", TemporalArchetype.TYPE_2_SHELF_DECAY,
            4, 7.5, shelf_life_days=90, warning_threshold_days=20, decay_model="MOISTURE_STRENGTH",
        )
        c2 = InventoryItem(
            "PORTLAND-ALT", "Cement", TemporalArchetype.TYPE_2_SHELF_DECAY,
            4, 7.5, shelf_life_days=90, warning_threshold_days=20, decay_model="MOISTURE_STRENGTH",
        )
        q1 = evaluate_lot(StockLot("1", c1, "b", 1, date(2026, 8, 20), "x"), self.today)[2]
        q2 = evaluate_lot(StockLot("2", c2, "b", 1, date(2026, 8, 20), "x"), self.today)[2]
        self.assertAlmostEqual(q1, q2, places=9)

    def test_configurable_maturation_curves(self) -> None:
        rice = InventoryItem(
            "RICE", "Rice", TemporalArchetype.TYPE_3_MATURING,
            2, 4, maturation_days=365, maturation_curve="SATURATING",
        )
        timber = InventoryItem(
            "TIMBER", "Oak", TemporalArchetype.TYPE_3_MATURING,
            2, 4, maturation_days=365, maturation_curve="SIGMOID",
        )
        q_rice = evaluate_lot(StockLot("r", rice, "b", 1, self.today - timedelta(days=120), "x"), self.today)[2]
        q_timber = evaluate_lot(StockLot("t", timber, "b", 1, self.today - timedelta(days=120), "x"), self.today)[2]
        self.assertNotAlmostEqual(q_rice, q_timber, places=2)

    def test_overaged_bell_curve_wine_locks_expired(self) -> None:
        ancient_wine = StockLot("OW", self.wine, "V2001", 5, date(2001, 1, 1), "Cellar")
        status, price, _ = evaluate_lot(ancient_wine, self.today)
        self.assertEqual(status, LotStatus.EXPIRED_LOCKED)
        self.assertEqual(price, Decimal("0.00"))

    def test_decimal_prices_enforced(self) -> None:
        fresh = StockLot("F1", self.milk, "B2", 10, date(2026, 11, 1), "Cooler")
        _, price, _ = evaluate_lot(fresh, self.today)
        self.assertIsInstance(price, Decimal)

    def test_reservation_commit_release_and_ttl_expiry(self) -> None:
        lot = StockLot("L1", self.milk, "B1", 20, date(2026, 11, 1), "Cooler")
        t0 = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)

        # Reserve 8 -> cancel -> full 20 available again
        r1 = fulfill_order([lot], 8, self.today, ttl_minutes=15, now=t0)
        self.assertEqual(lot.available_quantity, Decimal("12"))
        cancel_order(r1)
        self.assertEqual(lot.available_quantity, Decimal("20"))

        # Reserve 6 -> TTL expire after 20 mins -> full 20 available again
        r2 = fulfill_order([lot], 6, self.today, ttl_minutes=15, now=t0)
        expired = expire_stale_reservations([r2["reservation"]], now=t0 + timedelta(minutes=20))
        self.assertEqual(len(expired), 1)
        self.assertEqual(lot.available_quantity, Decimal("20"))

        # Reserve 5 -> Commit to Ledger -> lot.quantity becomes 15 and ledger records -5
        ledger = ImmutableStockLedger()
        ledger.post_movement("init", "MILK", "L1", "RECEIPT_GRN", 20, "GRN-1", "u", inbound_landed_unit_cost="1.50")
        r3 = fulfill_order([lot], 5, self.today, ttl_minutes=15, now=t0)
        commit_allocation(r3, ledger=ledger, reference_doc="POS-99", actor_id="cashier-1")
        self.assertEqual(lot.quantity, Decimal("15"))
        self.assertEqual(lot.allocated_quantity, Decimal("0"))
        self.assertEqual(ledger.lot_qty("MILK", "L1"), Decimal("15"))
        with self.assertRaises(ReservationStateError):
            commit_allocation(r3)


class TestImmutableLedgerAndERP(unittest.TestCase):
    def test_concurrent_over_issue_never_negative(self) -> None:
        led = ImmutableStockLedger()
        led.post_movement("r1", "S", "L1", "RECEIPT_GRN", 100, "GRN", "u", inbound_landed_unit_cost=1)
        errs = []

        def worker(i: int) -> None:
            try:
                led.post_movement(f"i{i}", "S", "L1", "ISSUE_OUT", -10, "SIS", "u")
            except ValueError:
                errs.append(i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(led.lot_qty("S", "L1"), Decimal("0"))
        self.assertEqual(len(errs), 15)

    def test_idempotency_replay_vs_conflict(self) -> None:
        led = ImmutableStockLedger()
        e1 = led.post_movement("k1", "S", "L1", "RECEIPT_GRN", 100, "GRN", "u", inbound_landed_unit_cost=1)
        e2 = led.post_movement("k1", "S", "L1", "RECEIPT_GRN", 100, "GRN", "u", inbound_landed_unit_cost=1)
        self.assertEqual(e1.ledger_id, e2.ledger_id)
        with self.assertRaises(IdempotencyConflictError):
            led.post_movement("k1", "S", "L1", "RECEIPT_GRN", 999, "GRN", "u", inbound_landed_unit_cost=1)

    def test_invalid_movement_type_and_wrong_sign_rejected(self) -> None:
        led = ImmutableStockLedger()
        led.post_movement("r1", "S", "L1", "RECEIPT_GRN", 100, "GRN", "u", inbound_landed_unit_cost=1)
        with self.assertRaises(InvalidMovementError):
            led.post_movement("bad-type", "S", "L1", "FOO_BAR", -1, "X", "u")
        with self.assertRaises(InvalidMovementError):
            led.post_movement("bad-sign-1", "S", "L1", "ISSUE_OUT", 5, "X", "u")
        with self.assertRaises(InvalidMovementError):
            led.post_movement("bad-sign-2", "S", "L1", "RECEIPT_GRN", -5, "X", "u", inbound_landed_unit_cost=1)

    def test_cross_sku_lot_corruption_rejected(self) -> None:
        led = ImmutableStockLedger()
        led.post_movement("r1", "CEMENT", "LOT-A", "RECEIPT_GRN", 100, "GRN", "u", inbound_landed_unit_cost=1)
        with self.assertRaises(LotSKUMismatchError):
            led.post_movement("r2", "SAND", "LOT-A", "RECEIPT_GRN", 50, "GRN", "u", inbound_landed_unit_cost=1)

    def test_site_return_uses_original_issue_cost_snapshot(self) -> None:
        led = ImmutableStockLedger()
        led.post_movement("a", "S", "L", "RECEIPT_GRN", 100, "G1", "u", inbound_landed_unit_cost=1)
        iss = led.post_movement("b", "S", "L", "ISSUE_OUT", -50, "I1", "u", wbs_task_code="WBS-1")
        led.post_movement("c", "S", "L", "RECEIPT_GRN", 50, "G2", "u", inbound_landed_unit_cost=3)
        ret = led.post_movement("d", "S", "L", "RETURN_FROM_SITE", 10, "R1", "u", wbs_task_code="WBS-1")
        self.assertEqual(ret.unit_cost_applied, iss.unit_cost_applied)
        self.assertEqual(ret.unit_cost_applied, Decimal("1.000000"))

    def test_atomic_stock_transfer(self) -> None:
        led = ImmutableStockLedger()
        led.post_movement("r1", "S", "YARD-A", "RECEIPT_GRN", 100, "G1", "u", inbound_landed_unit_cost="2.50")
        out_e, in_e = transfer_stock(led, "tx-1", "S", "YARD-A", "YARD-B", 40, "TR-01", "storekeeper")
        self.assertEqual(out_e.signed_base_qty, Decimal("-40"))
        self.assertEqual(in_e.signed_base_qty, Decimal("40"))
        self.assertEqual(led.lot_qty("S", "YARD-A"), Decimal("60"))
        self.assertEqual(led.lot_qty("S", "YARD-B"), Decimal("40"))
        self.assertEqual(led.sku_total_qty("S"), Decimal("100"))
        self.assertEqual(led.current_mwac("S"), Decimal("2.500000"))
        self.assertIs(post_transfer, transfer_stock)

    def test_replenishment_three_way_match_and_abc_xyz(self) -> None:
        ss = safety_stock(std_daily_demand=10, mean_lead_time_days=4, mean_daily_demand=50, std_lead_time_days=1)
        rop = reorder_point(mean_daily_demand=50, mean_lead_time_days=4, std_daily_demand=10, std_lead_time_days=1)
        self.assertGreater(ss, Decimal("0"))
        self.assertEqual(rop, Decimal("200.00") + ss)

        q_std = eoq(annual_demand=10000, order_setup_cost=50, annual_holding_cost_per_unit=2)
        q_per = eoq_perishable(10000, 50, 2, unit_purchase_cost=10, annual_deterioration_rate=0.3)
        self.assertLess(q_per, q_std)

        m_opt = eoq_maturing(
            annual_demand=3650,
            order_setup_cost=100,
            holding_cost_per_unit_day="0.005",
            unit_purchase_cost="2.00",
            price_at_age_fn=lambda d: 3.5 + 2.5 * (1 - 2.71828 ** (-d / 200.0)),
            min_age_days=1,
            max_age_days=900,
            step_days=1,
        )
        # Analytical optimum: 0.0125 * exp(-t/200) = 0.005 => t* = 200 * ln(2.5) ~= 183.26 days
        self.assertAlmostEqual(m_opt["optimal_maturation_days"], 183, delta=1)

        m_ok = three_way_match_po(100, "10.00", 98, 98, "10.00")
        self.assertTrue(m_ok["matched"])
        m_bad = three_way_match_po(100, "10.00", 95, 100, "11.00")
        self.assertFalse(m_bad["matched"])
        self.assertEqual(len(m_bad["discrepancies"]), 2)

        classified = abc_xyz_classify([
            {"sku": "HIGH-STEADY", "unit_cost": 100, "period_demands": [100, 102, 98, 100]},
            {"sku": "MID-ERRATIC", "unit_cost": 15, "period_demands": [20, 80, 10, 90]},
            {"sku": "LOW-TINY", "unit_cost": 1, "period_demands": [5, 5, 5, 5]},
        ])
        self.assertEqual(classified[0]["matrix_class"], "AX")
        self.assertEqual(classified[-1]["abc"], "C")

    def test_all_python_files_parse_and_cli_scripts_succeed(self) -> None:
        for py_file in SKILL_ROOT.rglob("*.py"):
            ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for cli, extra in [
            ("evaluate-stock.py", ["--json"]),
            ("dispatch-order.py", ["--demo", "--json"]),
            ("reconcile-ledger.py", ["--json"]),
        ]:
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / cli), *extra],
                capture_output=True,
                text=True,
                check=True,
            )
            parsed = json.loads(proc.stdout)
            self.assertIsInstance(parsed, dict)


class TestAllVerticalDomains(unittest.TestCase):
    def test_supermarket_scale_ean13_mrp_bundles_and_bogo(self) -> None:
        from engine import (
            build_scale_ean13,
            compute_bogo_quantities,
            compute_bundle_availability,
            enforce_mrp_ceiling,
            parse_scale_ean13,
        )

        # Weight barcode: PLU 01425, 1.375 kg
        bc_w = build_scale_ean13("20", "1425", Decimal("1.375"), decimals=3)
        parsed_w = parse_scale_ean13(bc_w)
        self.assertEqual(parsed_w["plu"], "01425")
        self.assertEqual(parsed_w["weight_kg"], Decimal("1.375000"))

        # Tampered check digit must fail
        bad = bc_w[:-1] + ("0" if bc_w[-1] != "0" else "1")
        with self.assertRaises(ValueError):
            parse_scale_ean13(bad)

        # Statutory batch MRP ceiling clamp
        self.assertEqual(enforce_mrp_ceiling("52.00", "45.00"), Decimal("45.00"))
        self.assertEqual(enforce_mrp_ceiling("38.00", "45.00"), Decimal("38.00"))

        # Bundles & BOGO
        self.assertEqual(
            compute_bundle_availability({"PASTA": 30, "SAUCE": 11, "CHEESE": 25}, {"PASTA": 2, "SAUCE": 1, "CHEESE": 1}),
            11,
        )
        bogo = compute_bogo_quantities(requested_paid_units=5, buy_x=2, get_y_free=1)
        self.assertEqual(bogo["free_promo_units"], 2)
        self.assertEqual(bogo["total_inventory_deducted"], 7)

    def test_apparel_variant_matrix_size_brokenness_and_returns(self) -> None:
        from engine import evaluate_size_curve_health, generate_variant_matrix, grade_customer_return

        variants = generate_variant_matrix(
            "POLO-402",
            "Cotton Polo",
            "25.00",
            {"color": ["Navy", "White"], "size": ["S", "M", "L", "XXL"]},
            price_surcharges={("size", "XXL"): "3.00"},
        )
        self.assertEqual(len(variants), 8)
        xxl = [v for v in variants if v["attributes"]["size"] == "XXL"][0]
        self.assertEqual(xxl["selling_price"], Decimal("28.00"))

        # Core sizes M & L stocked out -> size-broken!
        curve = {"S": 0.15, "M": 0.35, "L": 0.35, "XXL": 0.15}
        broken = evaluate_size_curve_health({"S": 12, "M": 0, "L": 0, "XXL": 9}, curve)
        self.assertTrue(broken["is_size_broken"])
        self.assertEqual(broken["missing_core_sizes"], ["L", "M"])
        self.assertGreater(broken["recommended_clearance_discount"], Decimal("0"))

        self.assertEqual(grade_customer_return("GRADE_B_OPEN_BOX", "100.00")["resale_price"], Decimal("80.00"))
        self.assertEqual(grade_customer_return("GRADE_C_DEFECTIVE", "100.00")["lifecycle_state"], "QUARANTINED")

    def test_electronics_imei_luhn_dual_warranty_and_rma_swap(self) -> None:
        from engine import SerializedUnitRegistry, validate_imei_luhn

        valid_imei = "490154203237518"  # Standard GSMA Luhn test IMEI
        self.assertTrue(validate_imei_luhn(valid_imei))
        self.assertFalse(validate_imei_luhn("490154203237519"))

        reg = SerializedUnitRegistry()
        reg.receive_unit(valid_imei, "PHONE-X", "LOT-P1", date(2026, 1, 1), 12, 12, is_imei=True)
        reg.receive_unit("356938035643809", "PHONE-X", "LOT-P1", date(2026, 6, 1), 12, 12, is_imei=True)
        with self.assertRaises(ValueError):
            reg.receive_unit(valid_imei, "PHONE-X", "LOT-P1", date(2026, 1, 1), is_imei=True)

        reg.sell_unit(valid_imei, date(2026, 5, 1), "INV-9001")
        rma = reg.open_rma(valid_imei, date(2026, 11, 3), "RMA-77")
        self.assertTrue(rma["customer_warranty_active"])
        self.assertTrue(rma["vendor_warranty_active"])

        old_u, new_u = reg.resolve_rma_with_swap(valid_imei, "356938035643809", date(2026, 11, 4))
        self.assertEqual(old_u.state, "REPLACED_CLOSED")
        self.assertEqual(new_u.state, "SOLD")
        self.assertEqual(new_u.customer_invoice_id, "INV-9001")

    def test_pharmacy_gs1_controlled_register_recall_bud_and_cold_chain(self) -> None:
        from engine import (
            ControlledSubstanceRegister,
            PharmacyComplianceError,
            TemperatureLog,
            build_near_expiry_return_claims,
            compute_open_container_bud,
            evaluate_cold_chain_excursion,
            gtin14_check_digit,
            parse_gs1_datamatrix,
        )

        gtin_body = "0890123456789"
        gtin = gtin_body + gtin14_check_digit(gtin_body)
        # 280200 -> Feb 2028 (leap year!) with DD=00 -> 2028-02-29
        parsed = parse_gs1_datamatrix(f"(01){gtin}(17)280200(10)BATCH-M9(21)SN-551")
        self.assertEqual(parsed["gtin14"], gtin)
        self.assertEqual(parsed["expiry_date"], date(2028, 2, 29))
        self.assertEqual(parsed["batch_number"], "BATCH-M9")
        self.assertEqual(parsed["serial_number"], "SN-551")

        # Raw FNC1 (\x1d) scan
        raw_parsed = parse_gs1_datamatrix(f"]d201{gtin}1727060010LOT77\x1d21SER88")
        self.assertEqual(raw_parsed["expiry_date"], date(2027, 6, 30))

        # Narcotics Register: dual-witness + Rx required + recall block + SHA-256 chain
        reg = ControlledSubstanceRegister()
        reg.record_event("MORPH-10MG", "NARCOTIC_SCHEDULE_X", "B-101", "RECEIPT", 50, "pharm-1", "WARD-ICU")

        # Missing or self-witness must fail
        with self.assertRaises(PharmacyComplianceError):
            reg.record_event(
                "MORPH-10MG", "NARCOTIC_SCHEDULE_X", "B-101", "DISPENSE", -2,
                "nurse-1", "WARD-ICU", witness_id="nurse-1",
                patient_id="P1", prescription_id="RX1", prescriber_reg_no="MD-99",
            )

        reg.record_event(
            "MORPH-10MG", "NARCOTIC_SCHEDULE_X", "B-101", "DISPENSE", -2,
            "nurse-1", "WARD-ICU", witness_id="nurse-2",
            patient_id="PAT-401", prescription_id="RX-881", prescriber_reg_no="MCI-4421",
        )
        self.assertTrue(reg.verify_chain_integrity())

        # Trigger batch recall -> blocks further dispenses & traces PAT-401
        recall_report = reg.trigger_batch_recall("MORPH-10MG", "B-101", "Particulate contamination")
        self.assertEqual(len(recall_report["affected_patient_dispenses"]), 1)
        self.assertEqual(recall_report["affected_patient_dispenses"][0]["patient_id"], "PAT-401")
        with self.assertRaises(PharmacyComplianceError):
            reg.record_event(
                "MORPH-10MG", "NARCOTIC_SCHEDULE_X", "B-101", "DISPENSE", -1,
                "nurse-1", "WARD-ICU", witness_id="nurse-2",
                patient_id="PAT-402", prescription_id="RX-882", prescriber_reg_no="MCI-4421",
            )

        # Open-vial BUD shortening
        bud = compute_open_container_bud(
            manufacturer_expiry=datetime(2027, 12, 31, tzinfo=timezone.utc),
            opened_at=datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
            in_use_stability_hours=28 * 24,
        )
        self.assertEqual(bud, datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc))

        # Cold-chain freeze breach quarantine
        t0 = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)
        cc = evaluate_cold_chain_excursion([TemperatureLog(t0, 4.0), TemperatureLog(t0, -1.5)])
        self.assertTrue(cc["quarantine_triggered"])
        self.assertEqual(cc["recommended_state"], "QUARANTINED")

        # Near-expiry distributor claim
        med = InventoryItem("AMOX", "Amoxicillin", TemporalArchetype.TYPE_2_SHELF_DECAY, "5.00", "9.00", shelf_life_days=60)
        lot = StockLot("L-AMOX", med, "BM-1", 40, date(2026, 10, 1), "PHARM-A")
        claims = build_near_expiry_return_claims([lot], date(2026, 11, 3))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["expected_credit_amount"], Decimal("170.00"))

    def test_bom_prep_interchange_rental_multicurrency_and_offline_sync(self) -> None:
        from engine import (
            CoreDepositTracker,
            PartInterchangeGraph,
            PrepIngredientInput,
            RentalAssetPool,
            compute_multicurrency_landed_cost,
            produce_prep_or_subassembly_batch,
            replay_offline_sync_batch,
        )

        # 1. Kitchen Multi-Level Prep Batch with 80% Yield
        led = ImmutableStockLedger()
        led.post_movement("g1", "RAW-TOMATO", "LT-1", "RECEIPT_GRN", 100, "GRN-1", "chef", inbound_landed_unit_cost="2.00")
        prep = produce_prep_or_subassembly_batch(
            ledger=led,
            work_order_id="WO-SAUCE-01",
            inputs=[PrepIngredientInput("RAW-TOMATO", "LT-1", 50)],
            output_sku="PREP-PIZZA-SAUCE",
            output_lot_id="LS-1",
            expected_raw_sum_qty=50,
            yield_pct=80,               # 50 kg raw -> 40 kg net sauce
            direct_conversion_cost="20.00",  # (50 * $2 + $20) / 40 kg = $3.00/kg
        )
        self.assertEqual(prep["output_qty"], Decimal("40.000000"))
        self.assertEqual(led.current_mwac("PREP-PIZZA-SAUCE"), Decimal("3.000000"))

        # 2. Auto-Parts Interchange & Core Deposit
        graph = PartInterchangeGraph()
        graph.register_supersession("OEM-OLD", "OEM-NEW")
        graph.register_equivalent("OEM-NEW", "BOSCH-AF1")
        matches = graph.find_compatible_stock("OEM-OLD", {"OEM-OLD": 0, "OEM-NEW": 3, "BOSCH-AF1": 12})
        self.assertEqual([m["sku"] for m in matches], ["OEM-NEW", "BOSCH-AF1"])

        cores = CoreDepositTracker()
        cores.issue_with_core_charge("INV-55", "BATT-75AH", qty=2, deposit_per_unit="15.00")
        ref = cores.return_dirty_core("INV-55", returned_qty=1)
        self.assertEqual(ref["refund_amount"], Decimal("15.00"))
        self.assertEqual(ref["remaining_open_cores"], 1)

        # 3. Equipment Rental Loop
        pool = RentalAssetPool()
        pool.register_asset("SCAF-01", "SCAFFOLD-TOWER", daily_rate="25.00", security_deposit="200.00", maintenance_interval_days=5)
        t_out = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
        pool.checkout("SCAF-01", "RNT-10", t_out, due_back_at=t_out + timedelta(days=3))
        bill = pool.checkin("SCAF-01", returned_at=t_out + timedelta(days=5), damage_fee="30.00")
        # 3 regular days ($75) + 2 overdue days @ 1.5x ($75) + $30 damage = $180; 5 days >= maintenance_interval 5 -> MAINTENANCE_LOCKED
        self.assertEqual(bill["total_charges"], Decimal("180.00"))
        self.assertEqual(bill["deposit_refund"], Decimal("170.00"))
        self.assertEqual(bill["next_asset_state"], "MAINTENANCE_LOCKED")

        # 4. Multi-Currency IAS-2 Tax-Stripped Landed Cost
        lc = compute_multicurrency_landed_cost(
            accepted_base_qty=100,
            foreign_unit_price="10.00",
            fx_rate_to_base_currency="84.00",  # 84,000 INR merchandise
            customs_duty_pct="10.0",           # +8,400 INR duty = 92,400
            recoverable_gst_vat_pct="18.0",    # 16,632 INR recoverable ITC -> NOT in inventory cost!
            freight_insurance_base_curr="3600.00",  # +3,600 freight = 96,000 -> 960.00 / unit
        )
        self.assertEqual(lc["landed_unit_cost"], Decimal("960.000000"))
        self.assertEqual(lc["recoverable_input_tax_credit"], Decimal("16632.00"))

        # 5. Offline Sync Conflict Queue
        sync_led = ImmutableStockLedger()
        sync_led.post_movement("init", "BOLT", "LB-1", "RECEIPT_GRN", 10, "GRN", "u", inbound_landed_unit_cost=1)
        batch_res = replay_offline_sync_batch(
            sync_led,
            [
                {"idempotency_key": "devB-2", "client_timestamp": "2026-11-03T10:02:00Z", "seq": 2, "sku": "BOLT", "lot_id": "LB-1", "movement_type": "ISSUE_OUT", "signed_base_qty": -7},
                {"idempotency_key": "devA-1", "client_timestamp": "2026-11-03T10:01:00Z", "seq": 1, "sku": "BOLT", "lot_id": "LB-1", "movement_type": "ISSUE_OUT", "signed_base_qty": -6},
            ],
        )
        # devA-1 (earlier timestamp) takes 6 -> leaves 4; devB-2 wants 7 -> isolated to SYNC_CONFLICT_REVIEW!
        self.assertEqual(batch_res["applied_count"], 1)
        self.assertEqual(batch_res["conflict_count"], 1)
        self.assertEqual(sync_led.lot_qty("BOLT", "LB-1"), Decimal("4"))


if __name__ == "__main__":
    unittest.main()
