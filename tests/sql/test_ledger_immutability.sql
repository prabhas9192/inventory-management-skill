\set ON_ERROR_STOP 0
INSERT INTO catalog_items (item_id, sku, name, base_uom, temporal_archetype, base_cost, moving_weighted_avg_cost, base_selling_price)
VALUES ('00000000-0000-0000-0000-000000000001', 'CEMT', 'Cement', 'KG', 'SHELF_DECAY', 0.14, 0, 0.2);
INSERT INTO inventory_temporal_lots (lot_id, item_id, batch_lot_number, storage_location_id, lot_landed_unit_cost, manufactured_date, current_dynamic_price)
VALUES ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-000000000001', 'B1', 'Y1', 0.15, '2026-10-01', 0.2);

\echo '== T1 receipt 1000 @0.15'
INSERT INTO stock_ledger_entries (idempotency_key, item_id, lot_id, movement_type, signed_base_qty, unit_cost_applied, reference_doc_type, reference_doc_id, actor_id)
VALUES ('k1', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1', 'RECEIPT_GRN', 1000, 0.15, 'GRN', 'G1', 'u');
\echo '== T2 receipt 1000 @0.25 -> expect MWAC 0.20'
INSERT INTO stock_ledger_entries (idempotency_key, item_id, lot_id, movement_type, signed_base_qty, unit_cost_applied, reference_doc_type, reference_doc_id, actor_id)
VALUES ('k2', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1', 'RECEIPT_GRN', 1000, 0.25, 'GRN', 'G2', 'u');
SELECT moving_weighted_avg_cost AS mwac FROM catalog_items;
SELECT quantity_on_hand FROM inventory_temporal_lots;

\echo '== T3 over-issue 5000 (expect reject)'
INSERT INTO stock_ledger_entries (idempotency_key, item_id, lot_id, movement_type, signed_base_qty, unit_cost_applied, reference_doc_type, reference_doc_id, actor_id)
VALUES ('k3', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1', 'ISSUE_OUT', -5000, 0.2, 'SIS', 'S1', 'u');

\echo '== T4 ISSUE_OUT with POSITIVE qty (expect reject)'
INSERT INTO stock_ledger_entries (idempotency_key, item_id, lot_id, movement_type, signed_base_qty, unit_cost_applied, reference_doc_type, reference_doc_id, actor_id)
VALUES ('k4', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000a1', 'ISSUE_OUT', 50, 0.2, 'SIS', 'S2', 'u');
SELECT quantity_on_hand AS qty_after_t4 FROM inventory_temporal_lots;

\echo '== T5 UPDATE ledger row (expect reject - immutability)'
UPDATE stock_ledger_entries SET signed_base_qty = 999999 WHERE idempotency_key = 'k1';
\echo '== T6 DELETE ledger row (expect reject)'
DELETE FROM stock_ledger_entries WHERE idempotency_key = 'k1';
\echo '== T7 TRUNCATE ledger (expect reject)'
BEGIN; TRUNCATE stock_ledger_entries; SELECT count(*) AS rows_after_truncate FROM stock_ledger_entries; ROLLBACK;

\echo '== T8 direct UPDATE of lot qty bypassing ledger (expect reject)'
UPDATE inventory_temporal_lots SET quantity_on_hand = 1;
SELECT quantity_on_hand AS qty_after_bypass FROM inventory_temporal_lots;

\echo '== T9 ledger item_id mismatched with lot item (expect reject)'
INSERT INTO catalog_items (item_id, sku, name, temporal_archetype, base_cost, moving_weighted_avg_cost, base_selling_price)
VALUES ('00000000-0000-0000-0000-000000000002', 'SAND', 'Sand', 'INERT', 1, 0, 1);
INSERT INTO stock_ledger_entries (idempotency_key, item_id, lot_id, movement_type, signed_base_qty, unit_cost_applied, reference_doc_type, reference_doc_id, actor_id)
VALUES ('k9', '00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-0000000000a1', 'RECEIPT_GRN', 10, 1, 'GRN', 'G9', 'u');
SELECT sku, moving_weighted_avg_cost FROM catalog_items ORDER BY sku;
