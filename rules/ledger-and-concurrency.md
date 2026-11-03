# Rule: Immutable Stock Movement Ledger & Concurrency Locks

Never mutate `quantity_on_hand` in place without recording an immutable ledger entry and acquiring row-level locks. Direct `UPDATE lots SET qty = qty - 10` causes race conditions, lost audit trails, and phantom negative stock when two operators scan simultaneously.

---

## Incorrect vs. Correct Patterns

### ❌ Incorrect: Unsynchronized In-Place Quantity Mutation
```python
# BROKEN: Race condition (lost update / negative stock) + zero audit trail!
lot = db.query("SELECT quantity_on_hand FROM lots WHERE lot_id = %s", [lot_id])
if lot.quantity_on_hand >= req_qty:
    # Two concurrent requests both pass the check above and double-issue stock!
    db.execute("UPDATE lots SET quantity_on_hand = quantity_on_hand - %s WHERE lot_id = %s", [req_qty, lot_id])
```

### ✅ Correct: Ordered Row Locking + Idempotent Append-Only Ledger
```sql
BEGIN;

-- 1. Lock candidate lots in deterministic PK order to prevent deadlocks
SELECT lot_id, quantity_on_hand, quantity_allocated
FROM inventory_temporal_lots
WHERE item_id = $1
  AND lifecycle_state NOT IN ('AGING_LOCKED', 'EXPIRED_LOCKED', 'QUARANTINED')
  AND (quantity_on_hand - quantity_allocated) > 0
ORDER BY lot_id ASC
FOR UPDATE;

-- 2. Insert immutable movement (idempotency_key prevents duplicate retries on flaky site Wi-Fi)
INSERT INTO stock_ledger_entries (
    idempotency_key, lot_id, item_id, movement_type,
    Signed_base_qty, unit_cost_snapshot, reference_doc_type, reference_doc_id, actor_id
) VALUES (
    $2, $3, $1, 'ISSUE_OUT',
    -$4, $5, 'SITE_ISSUE_SLIP', $6, $7
);
-- Trigger automatically updates lot quantity_on_hand and enforces CHECK (quantity_on_hand >= 0)

COMMIT;
```

---

## Mandatory Ledger Invariants

1. **Every Stock Change Is an Event:** Valid `movement_type` values:
   - `RECEIPT_GRN` (`+`)
   - `RETURN_FROM_SITE` (`+`)
   - `ISSUE_OUT` (`-`)
   - `TRANSFER_OUT` (`-`) / `TRANSFER_IN` (`+`)
   - `CYCLE_COUNT_ADJUST` (`+` or `-`)
   - `SCRAP_EXPIRED` (`-`)
2. **Idempotency Keys Are Required on Mobile/Field Clients:** Every scan/issue request carries a client-generated UUID `idempotency_key` (`UNIQUE` index). Retrying after an offline disconnect returns the original receipt instead of double-deducting inventory.
3. **Deterministic Lock Ordering:** When fulfilling multi-line requisitions, always acquire `FOR UPDATE` locks ordered by `item_id ASC, lot_id ASC` to guarantee zero deadlocks.
