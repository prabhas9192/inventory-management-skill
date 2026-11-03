\set ON_ERROR_STOP 1
-- 1. Seed item & two lots (Yard A & Yard B) with statutory MRP ceiling = 10.00
INSERT INTO catalog_items (item_id, sku, name, base_uom, temporal_archetype, base_cost, moving_weighted_avg_cost, base_selling_price)
VALUES ('00000000-0000-0000-0000-000000000010', 'MORPH-10MG', 'Morphine 10mg', 'VIAL', 'SHELF_DECAY', 4.0, 0.0, 8.0);

INSERT INTO inventory_temporal_lots (lot_id, item_id, batch_lot_number, storage_location_id, lot_landed_unit_cost, manufactured_date, printed_mrp, current_dynamic_price)
VALUES
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-000000000010', 'BATCH-R1', 'PHARM-MAIN', 4.0, '2026-10-01', 10.0, 8.0),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-000000000010', 'BATCH-R1', 'WARD-ICU', 4.0, '2026-10-01', 10.0, 8.0);

-- 2. Post GRN receipt of 100 vials into PHARM-MAIN
INSERT INTO stock_ledger_entries (idempotency_key, item_id, lot_id, movement_type, signed_base_qty, unit_cost_applied, reference_doc_type, reference_doc_id, actor_id)
VALUES ('grn-m1', '00000000-0000-0000-0000-000000000010', '00000000-0000-0000-0000-0000000000b1', 'RECEIPT_GRN', 100, 4.0, 'GRN', 'G-1', 'pharm-1');

-- 3. Execute atomic sp_transfer_stock (move 30 vials from PHARM-MAIN to WARD-ICU)
CALL sp_transfer_stock('tr-icu-1', '00000000-0000-0000-0000-000000000010', '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000b2', 30, 'TR-100', 'pharm-1');

SELECT storage_location_id, quantity_on_hand FROM inventory_temporal_lots ORDER BY storage_location_id;

-- 4. Create active stock reservation on WARD-ICU lot, then trigger sp_execute_batch_recall -> must quarantine BOTH lots and release reservation!
UPDATE inventory_temporal_lots SET quantity_allocated = 5 WHERE lot_id = '00000000-0000-0000-0000-0000000000b2';
INSERT INTO stock_reservations (order_reference, lot_id, item_id, reserved_base_qty, locked_unit_price, expires_at)
VALUES ('ORD-ICU-9', '00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-000000000010', 5, 8.0, NOW() + INTERVAL '30 minutes');

CALL sp_execute_batch_recall('00000000-0000-0000-0000-000000000010', 'BATCH-R1', 'FDA Class I Recall');

SELECT q.storage_location_id, q.lifecycle_state, l.quantity_allocated, q.quantity_available, q.dispatch_eligibility
FROM view_smart_dispatch_queue q
JOIN inventory_temporal_lots l USING (lot_id)
ORDER BY q.storage_location_id;
SELECT status FROM stock_reservations WHERE order_reference = 'ORD-ICU-9';

-- 5. Verify JIT dispatch trigger computes target_dispatch_time cleanly
INSERT INTO jit_dispatch_orders (project_site_id, wbs_task_milestone_id, item_id, required_quantity, scheduled_consumption_time, estimated_transit_minutes, buffer_margin_minutes)
VALUES ('SITE-1', 'SLAB-3', '00000000-0000-0000-0000-000000000010', 10, '2026-11-03 06:00:00+00', 45, 15);
SELECT scheduled_consumption_time - target_dispatch_time AS jit_lead_interval FROM jit_dispatch_orders;

-- 6. Verify Narcotics Register: valid dual-witness succeeds; self-witness fails; UPDATE/TRUNCATE fails; MRP violation fails
INSERT INTO pharmacy_controlled_register (item_id, batch_lot_number, drug_schedule, movement_type, signed_qty, running_balance, actor_id, witness_id, patient_id, prescription_id, prescriber_reg_no, location_id, prev_hash, entry_hash)
VALUES ('00000000-0000-0000-0000-000000000010', 'BATCH-OK', 'NARCOTIC_SCHEDULE_X', 'DISPENSE', -2, 8, 'nurse-1', 'nurse-2', 'PAT-1', 'RX-1', 'MD-1', 'WARD-ICU', repeat('0', 64), repeat('a', 64));

\set ON_ERROR_STOP 0
\echo '== Expect reject: self-witnessed narcotic dispense'
INSERT INTO pharmacy_controlled_register (item_id, batch_lot_number, drug_schedule, movement_type, signed_qty, running_balance, actor_id, witness_id, patient_id, prescription_id, prescriber_reg_no, location_id, prev_hash, entry_hash)
VALUES ('00000000-0000-0000-0000-000000000010', 'BATCH-OK', 'NARCOTIC_SCHEDULE_X', 'DISPENSE', -1, 7, 'nurse-1', 'nurse-1', 'PAT-1', 'RX-2', 'MD-1', 'WARD-ICU', repeat('a', 64), repeat('b', 64));

\echo '== Expect reject: TRUNCATE on pharmacy_controlled_register'
TRUNCATE pharmacy_controlled_register;

\echo '== Expect reject: price above batch printed_mrp (12.00 > 10.00)'
UPDATE inventory_temporal_lots SET current_dynamic_price = 12.0 WHERE lot_id = '00000000-0000-0000-0000-0000000000b1';
