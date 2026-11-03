-- Production PostgreSQL 16+ Schema: Temporal Inventory + Immutable ERP Ledger, Multi-UOM, GRN & COGS
-- Verified against PostgreSQL 16 with row-level & statement-level immutability guards.

CREATE TABLE catalog_items (
    item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    base_uom VARCHAR(16) NOT NULL DEFAULT 'UNIT', -- Canonical atomic UOM: 'KG', 'LITER', 'METER', 'PIECE'
    temporal_archetype VARCHAR(16) NOT NULL CHECK (
        temporal_archetype IN ('RAPID_SPOIL', 'SHELF_DECAY', 'MATURING', 'BELL_CURVE', 'INERT')
    ),
    decay_model VARCHAR(24) NOT NULL DEFAULT 'WEIBULL' CHECK (
        decay_model IN ('LINEAR', 'WEIBULL', 'MOISTURE_STRENGTH', 'ARRHENIUS', 'NONE')
    ),
    maturation_curve VARCHAR(24) NOT NULL DEFAULT 'SATURATING' CHECK (
        maturation_curve IN ('SATURATING', 'LOGARITHMIC', 'SIGMOID', 'NONE')
    ),
    cost_accounting_method VARCHAR(24) NOT NULL DEFAULT 'MOVING_WEIGHTED_AVG' CHECK (
        cost_accounting_method IN ('MOVING_WEIGHTED_AVG', 'FIFO_COST_LAYER', 'SPECIFIC_LOT_COST')
    ),
    base_cost NUMERIC(16, 6) NOT NULL CHECK (base_cost >= 0),
    moving_weighted_avg_cost NUMERIC(16, 6) NOT NULL CHECK (moving_weighted_avg_cost >= 0),
    base_selling_price NUMERIC(16, 6) NOT NULL CHECK (base_selling_price >= 0),
    allowable_wastage_pct NUMERIC(5, 2) NOT NULL DEFAULT 2.00 CHECK (allowable_wastage_pct >= 0),
    shelf_life_days INT NULL CHECK (shelf_life_days IS NULL OR shelf_life_days > 0),
    warning_threshold_days INT NOT NULL DEFAULT 7 CHECK (warning_threshold_days >= 0),
    maturation_duration_days INT NOT NULL DEFAULT 0 CHECK (maturation_duration_days >= 0),
    peak_quality_window_days INT NOT NULL DEFAULT 0 CHECK (peak_quality_window_days >= 0),
    appreciation_multiplier NUMERIC(5, 2) NOT NULL DEFAULT 1.00 CHECK (appreciation_multiplier >= 1.00)
);

-- Multi-UOM Packaging & Conversion Factors (All quantities convert cleanly to catalog_items.base_uom)
CREATE TABLE uom_conversions (
    conversion_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES catalog_items(item_id) ON DELETE CASCADE,
    from_uom VARCHAR(24) NOT NULL,
    to_base_multiplier NUMERIC(16, 6) NOT NULL CHECK (to_base_multiplier > 0),
    UNIQUE (item_id, from_uom)
);

-- BOQ / BOM Composite Recipe Norms
CREATE TABLE boq_recipe_components (
    recipe_code VARCHAR(64) NOT NULL,
    component_item_id UUID NOT NULL REFERENCES catalog_items(item_id),
    norm_base_qty NUMERIC(16, 6) NOT NULL CHECK (norm_base_qty > 0),
    PRIMARY KEY (recipe_code, component_item_id)
);

-- Temporal Stock Lots (Composite UNIQUE (lot_id, item_id) prevents cross-SKU ledger corruption)
CREATE TABLE inventory_temporal_lots (
    lot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES catalog_items(item_id),
    batch_lot_number VARCHAR(64) NOT NULL,
    storage_location_id VARCHAR(64) NOT NULL,
    quantity_on_hand NUMERIC(16, 6) NOT NULL DEFAULT 0 CHECK (quantity_on_hand >= 0),
    quantity_allocated NUMERIC(16, 6) NOT NULL DEFAULT 0 CHECK (quantity_allocated >= 0),
    lot_landed_unit_cost NUMERIC(16, 6) NOT NULL CHECK (lot_landed_unit_cost >= 0),
    manufactured_date DATE NOT NULL,
    expiry_date DATE,
    opened_bud_expiry_at TIMESTAMP WITH TIME ZONE,
    printed_mrp NUMERIC(16, 6) NULL CHECK (printed_mrp IS NULL OR printed_mrp > 0),
    lifecycle_state VARCHAR(32) NOT NULL DEFAULT 'NORMAL_AVAILABLE' CHECK (
        lifecycle_state IN (
            'AGING_LOCKED', 'PRIME_MATURED', 'NORMAL_AVAILABLE',
            'WARNING_DISCOUNT', 'EXPIRED_LOCKED', 'QUARANTINED'
        )
    ),
    current_dynamic_price NUMERIC(16, 6) NOT NULL CHECK (current_dynamic_price >= 0),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (lot_id, item_id),
    CONSTRAINT chk_allocated_lte_on_hand CHECK (quantity_allocated <= quantity_on_hand),
    CONSTRAINT chk_mrp_ceiling CHECK (printed_mrp IS NULL OR current_dynamic_price <= printed_mrp)
);

-- Stock Reservations (Reserve -> Commit to Ledger or Cancel/Expire)
CREATE TABLE stock_reservations (
    reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_reference VARCHAR(64) NOT NULL,
    lot_id UUID NOT NULL,
    item_id UUID NOT NULL,
    reserved_base_qty NUMERIC(16, 6) NOT NULL CHECK (reserved_base_qty > 0),
    locked_unit_price NUMERIC(16, 6) NOT NULL CHECK (locked_unit_price >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (
        status IN ('ACTIVE', 'COMMITTED', 'RELEASED', 'EXPIRED')
    ),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    FOREIGN KEY (lot_id, item_id) REFERENCES inventory_temporal_lots(lot_id, item_id)
);

-- Inbound Goods Received Note (GRN) with Weighbridge & Landed Freight
CREATE TABLE goods_received_notes (
    grn_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grn_number VARCHAR(64) UNIQUE NOT NULL,
    po_reference VARCHAR(64),
    vendor_id VARCHAR(64) NOT NULL,
    vehicle_number VARCHAR(32),
    weighbridge_gross_kg NUMERIC(16, 3),
    weighbridge_tare_kg NUMERIC(16, 3),
    freight_and_unloading_cost NUMERIC(14, 2) NOT NULL DEFAULT 0.00 CHECK (freight_and_unloading_cost >= 0),
    received_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE grn_lines (
    grn_line_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grn_id UUID NOT NULL REFERENCES goods_received_notes(grn_id) ON DELETE CASCADE,
    item_id UUID NOT NULL,
    lot_id UUID NOT NULL,
    po_ordered_base_qty NUMERIC(16, 6) NOT NULL,
    challan_billed_base_qty NUMERIC(16, 6) NOT NULL,
    measured_moisture_pct NUMERIC(5, 2) NOT NULL DEFAULT 0.00,
    qc_rejected_base_qty NUMERIC(16, 6) NOT NULL DEFAULT 0.00,
    accepted_base_qty NUMERIC(16, 6) NOT NULL CHECK (accepted_base_qty >= 0),
    vendor_unit_price NUMERIC(16, 6) NOT NULL CHECK (vendor_unit_price >= 0),
    allocated_landed_cost NUMERIC(16, 6) NOT NULL DEFAULT 0.00,
    landed_unit_cost NUMERIC(16, 6) NOT NULL CHECK (landed_unit_cost >= 0),
    FOREIGN KEY (lot_id, item_id) REFERENCES inventory_temporal_lots(lot_id, item_id)
);

-- Immutable Append-Only Stock Movement Ledger (Single Source of Truth for Balances & COGS)
CREATE TABLE stock_ledger_entries (
    ledger_id BIGSERIAL PRIMARY KEY,
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,
    item_id UUID NOT NULL,
    lot_id UUID NOT NULL,
    movement_type VARCHAR(24) NOT NULL CHECK (
        movement_type IN (
            'RECEIPT_GRN', 'RETURN_FROM_SITE', 'TRANSFER_IN',
            'ISSUE_OUT', 'TRANSFER_OUT', 'RETURN_TO_VENDOR',
            'CYCLE_COUNT_ADJUST', 'SCRAP_EXPIRED'
        )
    ),
    signed_base_qty NUMERIC(16, 6) NOT NULL,
    unit_cost_applied NUMERIC(16, 6) NOT NULL CHECK (unit_cost_applied >= 0),
    total_cost_impact NUMERIC(18, 6) GENERATED ALWAYS AS (signed_base_qty * unit_cost_applied) STORED,
    reference_doc_type VARCHAR(32) NOT NULL,
    reference_doc_id VARCHAR(64) NOT NULL,
    wbs_task_code VARCHAR(64),
    actor_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    -- Composite FK guarantees a receipt/issue can NEVER land on another SKU's lot
    FOREIGN KEY (lot_id, item_id) REFERENCES inventory_temporal_lots(lot_id, item_id),
    -- Strict sign invariant per movement type
    CONSTRAINT chk_movement_sign CHECK (
        (movement_type IN ('RECEIPT_GRN', 'RETURN_FROM_SITE', 'TRANSFER_IN') AND signed_base_qty > 0)
        OR (movement_type IN ('ISSUE_OUT', 'TRANSFER_OUT', 'RETURN_TO_VENDOR', 'SCRAP_EXPIRED') AND signed_base_qty < 0)
        OR (movement_type = 'CYCLE_COUNT_ADJUST' AND signed_base_qty <> 0)
    )
);

-- Guard 1: Prohibit direct UPDATE of quantity_on_hand or DELETE/TRUNCATE on lots outside ledger trigger
CREATE OR REPLACE FUNCTION fn_guard_lot_balance_mutation() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.quantity_on_hand <> 0 THEN
        RAISE EXCEPTION 'New lots must initialize with quantity_on_hand = 0; post a RECEIPT_GRN to stock_ledger_entries';
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.quantity_on_hand IS DISTINCT FROM OLD.quantity_on_hand THEN
        IF coalesce(current_setting('inventory.ledger_write', true), 'off') <> 'on' THEN
            RAISE EXCEPTION 'Direct UPDATE of quantity_on_hand is prohibited; insert into stock_ledger_entries instead';
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.quantity_on_hand > 0 OR EXISTS (SELECT 1 FROM stock_ledger_entries WHERE lot_id = OLD.lot_id) THEN
            RAISE EXCEPTION 'Cannot delete lot % with stock or ledger history', OLD.lot_id;
        END IF;
        RETURN OLD;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_guard_lot_balance_mutation
BEFORE INSERT OR UPDATE OR DELETE ON inventory_temporal_lots
FOR EACH ROW EXECUTE FUNCTION fn_guard_lot_balance_mutation();

-- Guard 2: Statement-level TRUNCATE block on immutable tables
CREATE OR REPLACE FUNCTION fn_prohibit_truncate() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% is an immutable inventory table: TRUNCATE is prohibited', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_no_truncate_ledger
BEFORE TRUNCATE ON stock_ledger_entries
FOR EACH STATEMENT EXECUTE FUNCTION fn_prohibit_truncate();

CREATE TRIGGER trg_no_truncate_lots
BEFORE TRUNCATE ON inventory_temporal_lots
FOR EACH STATEMENT EXECUTE FUNCTION fn_prohibit_truncate();

-- Trigger: Serialize on catalog_items FIRST, update MWAC, and atomically update lot balance
CREATE OR REPLACE FUNCTION fn_apply_stock_ledger_entry() RETURNS TRIGGER AS $$
DECLARE
    v_old_item_qty NUMERIC(16, 6);
    v_old_mwac NUMERIC(16, 6);
    v_new_mwac NUMERIC(16, 6);
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'stock_ledger_entries is immutable: UPDATE and DELETE are prohibited';
    END IF;

    -- 1. Lock the parent item row FIRST to serialize concurrent receipts/returns and eliminate MWAC races
    SELECT moving_weighted_avg_cost INTO v_old_mwac
    FROM catalog_items WHERE item_id = NEW.item_id FOR UPDATE;

    -- 2. Recompute MWAC on positive cost-bearing inbound receipts & site returns
    IF NEW.movement_type IN ('RECEIPT_GRN', 'RETURN_FROM_SITE') AND NEW.signed_base_qty > 0 THEN
        SELECT COALESCE(SUM(quantity_on_hand), 0) INTO v_old_item_qty
        FROM inventory_temporal_lots WHERE item_id = NEW.item_id;

        v_new_mwac := ((v_old_item_qty * v_old_mwac) + (NEW.signed_base_qty * NEW.unit_cost_applied))
                      / (v_old_item_qty + NEW.signed_base_qty);

        UPDATE catalog_items
        SET moving_weighted_avg_cost = v_new_mwac
        WHERE item_id = NEW.item_id;
    END IF;

    -- 3. Authorize controlled lot balance update inside transaction-local GUC scope
    PERFORM set_config('inventory.ledger_write', 'on', true);
    UPDATE inventory_temporal_lots
    SET quantity_on_hand = quantity_on_hand + NEW.signed_base_qty
    WHERE lot_id = NEW.lot_id AND item_id = NEW.item_id;
    PERFORM set_config('inventory.ledger_write', 'off', true);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_apply_stock_ledger_entry
BEFORE INSERT OR UPDATE OR DELETE ON stock_ledger_entries
FOR EACH ROW EXECUTE FUNCTION fn_apply_stock_ledger_entry();

-- Atomic Paired Inter-Site / Inter-Bin Stock Transfer Procedure
CREATE OR REPLACE PROCEDURE sp_transfer_stock(
    p_idempotency_prefix VARCHAR,
    p_item_id UUID,
    p_from_lot_id UUID,
    p_to_lot_id UUID,
    p_qty NUMERIC(16, 6),
    p_ref_doc_id VARCHAR,
    p_actor_id VARCHAR
) LANGUAGE plpgsql AS $$
DECLARE
    v_cost NUMERIC(16, 6);
BEGIN
    IF p_qty <= 0 THEN
        RAISE EXCEPTION 'Transfer quantity must be > 0';
    END IF;
    IF p_from_lot_id = p_to_lot_id THEN
        RAISE EXCEPTION 'Source and destination lot cannot be identical';
    END IF;

    SELECT moving_weighted_avg_cost INTO v_cost
    FROM catalog_items WHERE item_id = p_item_id FOR UPDATE;

    INSERT INTO stock_ledger_entries (
        idempotency_key, item_id, lot_id, movement_type,
        signed_base_qty, unit_cost_applied, reference_doc_type, reference_doc_id, actor_id
    ) VALUES (
        p_idempotency_prefix || ':OUT', p_item_id, p_from_lot_id, 'TRANSFER_OUT',
        -p_qty, v_cost, 'STOCK_TRANSFER', p_ref_doc_id, p_actor_id
    );

    INSERT INTO stock_ledger_entries (
        idempotency_key, item_id, lot_id, movement_type,
        signed_base_qty, unit_cost_applied, reference_doc_type, reference_doc_id, actor_id
    ) VALUES (
        p_idempotency_prefix || ':IN', p_item_id, p_to_lot_id, 'TRANSFER_IN',
        p_qty, v_cost, 'STOCK_TRANSFER', p_ref_doc_id, p_actor_id
    );
END;
$$;

-- Blind Physical Cycle Count Audits
CREATE TABLE cycle_count_audits (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lot_id UUID NOT NULL REFERENCES inventory_temporal_lots(lot_id),
    system_frozen_base_qty NUMERIC(16, 6) NOT NULL,
    blind_counted_base_qty NUMERIC(16, 6) NOT NULL,
    variance_base_qty NUMERIC(16, 6) GENERATED ALWAYS AS (blind_counted_base_qty - system_frozen_base_qty) STORED,
    reason_code VARCHAR(64),
    approved_by VARCHAR(64),
    counted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Construction BOQ Wastage Reconciliation per WBS Work Package
CREATE TABLE boq_wastage_reconciliations (
    recon_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wbs_task_code VARCHAR(64) NOT NULL,
    item_id UUID NOT NULL REFERENCES catalog_items(item_id),
    theoretical_boq_base_qty NUMERIC(16, 6) NOT NULL CHECK (theoretical_boq_base_qty > 0),
    allowable_wastage_pct NUMERIC(5, 2) NOT NULL,
    actual_net_issued_base_qty NUMERIC(16, 6) NOT NULL,
    excess_wastage_pct NUMERIC(6, 2) GENERATED ALWAYS AS (
        ((actual_net_issued_base_qty - (theoretical_boq_base_qty * (1 + allowable_wastage_pct / 100.0)))
         / theoretical_boq_base_qty) * 100.0
    ) STORED,
    reconciled_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Just-In-Time (JIT) Milestone Dispatch Orders (Uses BEFORE trigger so timestamptz arithmetic works on all PG versions)
CREATE TABLE jit_dispatch_orders (
    dispatch_order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_site_id VARCHAR(64) NOT NULL,
    wbs_task_milestone_id VARCHAR(64) NOT NULL,
    item_id UUID NOT NULL REFERENCES catalog_items(item_id),
    required_quantity NUMERIC(16, 6) NOT NULL CHECK (required_quantity > 0),
    scheduled_consumption_time TIMESTAMP WITH TIME ZONE NOT NULL,
    estimated_transit_minutes INT NOT NULL CHECK (estimated_transit_minutes >= 0),
    buffer_margin_minutes INT NOT NULL DEFAULT 45 CHECK (buffer_margin_minutes >= 0),
    target_dispatch_time TIMESTAMP WITH TIME ZONE NOT NULL,
    dispatch_status VARCHAR(32) NOT NULL DEFAULT 'PENDING_DISPATCH'
);

CREATE OR REPLACE FUNCTION fn_compute_jit_dispatch_time() RETURNS TRIGGER AS $$
BEGIN
    NEW.target_dispatch_time := NEW.scheduled_consumption_time
        - make_interval(mins => NEW.estimated_transit_minutes + NEW.buffer_margin_minutes);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_compute_jit_dispatch_time
BEFORE INSERT OR UPDATE OF scheduled_consumption_time, estimated_transit_minutes, buffer_margin_minutes
ON jit_dispatch_orders
FOR EACH ROW EXECUTE FUNCTION fn_compute_jit_dispatch_time();

-- Smart Dispatch Queue View
CREATE OR REPLACE VIEW view_smart_dispatch_queue AS
SELECT 
    l.lot_id,
    c.sku,
    c.name,
    c.base_uom,
    c.temporal_archetype,
    c.cost_accounting_method,
    c.moving_weighted_avg_cost,
    l.batch_lot_number,
    l.storage_location_id,
    (l.quantity_on_hand - l.quantity_allocated) AS quantity_available,
    l.lot_landed_unit_cost,
    l.manufactured_date,
    (CURRENT_DATE - l.manufactured_date) AS age_days,
    l.expiry_date,
    (l.expiry_date - CURRENT_DATE) AS days_until_expiry,
    l.lifecycle_state,
    l.current_dynamic_price,
    CASE 
        WHEN l.lifecycle_state IN ('EXPIRED_LOCKED', 'QUARANTINED') THEN 'HOLD_LOCKED'
        WHEN c.temporal_archetype IN ('MATURING', 'BELL_CURVE')
             AND (CURRENT_DATE - l.manufactured_date) < c.maturation_duration_days THEN 'HOLD_MATURING'
        WHEN l.opened_bud_expiry_at IS NOT NULL AND NOW() >= l.opened_bud_expiry_at THEN 'HOLD_EXPIRED'
        WHEN l.expiry_date IS NOT NULL AND CURRENT_DATE >= l.expiry_date THEN 'HOLD_EXPIRED'
        WHEN (l.quantity_on_hand - l.quantity_allocated) <= 0 THEN 'DEPLETED'
        ELSE 'AVAILABLE_FOR_DISPATCH'
    END AS dispatch_eligibility
FROM inventory_temporal_lots l
JOIN catalog_items c ON l.item_id = c.item_id;

-- ============================================================================
-- VERTICAL DOMAIN EXTENSIONS (Apparel, Electronics, Pharmacy, Auto Parts, Rental, Sync)
-- ============================================================================

-- 1. Apparel / Footwear Style-Color-Size Variant Matrix
CREATE TABLE item_style_variants (
    variant_item_id UUID PRIMARY KEY REFERENCES catalog_items(item_id) ON DELETE CASCADE,
    parent_style_code VARCHAR(64) NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb  -- e.g., {"color": "Navy", "size": "M"}
);

-- 2. Serialized Units / IMEI & Dual Warranty Registry (Electronics, Machinery)
CREATE TABLE serialized_units (
    serial_number VARCHAR(64) PRIMARY KEY,
    item_id UUID NOT NULL,
    lot_id UUID NOT NULL,
    is_imei BOOLEAN NOT NULL DEFAULT FALSE,
    unit_state VARCHAR(28) NOT NULL DEFAULT 'IN_STOCK' CHECK (
        unit_state IN (
            'IN_STOCK', 'RESERVED', 'SOLD',
            'RMA_CUSTOMER_RETURN', 'AT_VENDOR_SERVICE', 'REPLACED_CLOSED', 'SCRAPPED'
        )
    ),
    vendor_warranty_expiry DATE NOT NULL,
    customer_warranty_expiry DATE,
    customer_invoice_id VARCHAR(64),
    rma_ticket_id VARCHAR(64),
    replacement_serial VARCHAR(64) REFERENCES serialized_units(serial_number),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    FOREIGN KEY (lot_id, item_id) REFERENCES inventory_temporal_lots(lot_id, item_id)
);

-- 3. Pharmacy Schedule H / H1 / Narcotics (Schedule X) Immutable Register & Batch Recalls
CREATE TABLE pharmacy_controlled_register (
    seq_no BIGSERIAL PRIMARY KEY,
    item_id UUID NOT NULL REFERENCES catalog_items(item_id),
    batch_lot_number VARCHAR(64) NOT NULL,
    drug_schedule VARCHAR(24) NOT NULL CHECK (
        drug_schedule IN ('OTC', 'SCHEDULE_H', 'SCHEDULE_H1', 'NARCOTIC_SCHEDULE_X')
    ),
    movement_type VARCHAR(24) NOT NULL CHECK (
        movement_type IN ('RECEIPT', 'DISPENSE', 'WASTAGE_DESTRUCTION', 'RETURN')
    ),
    signed_qty NUMERIC(16, 6) NOT NULL CHECK (signed_qty <> 0),
    running_balance NUMERIC(16, 6) NOT NULL CHECK (running_balance >= 0),
    actor_id VARCHAR(64) NOT NULL,
    witness_id VARCHAR(64),
    patient_id VARCHAR(64),
    prescription_id VARCHAR(64),
    prescriber_reg_no VARCHAR(64),
    location_id VARCHAR(64) NOT NULL,
    prev_hash CHAR(64) NOT NULL,
    entry_hash CHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT chk_rx_linkage CHECK (
        movement_type <> 'DISPENSE'
        OR drug_schedule = 'OTC'
        OR (patient_id IS NOT NULL AND prescription_id IS NOT NULL AND prescriber_reg_no IS NOT NULL)
    ),
    CONSTRAINT chk_narcotic_dual_witness CHECK (
        drug_schedule <> 'NARCOTIC_SCHEDULE_X'
        OR movement_type NOT IN ('DISPENSE', 'WASTAGE_DESTRUCTION')
        OR (witness_id IS NOT NULL AND witness_id <> actor_id)
    )
);

CREATE OR REPLACE FUNCTION fn_prohibit_register_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'pharmacy_controlled_register is legally immutable: % prohibited', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_no_mutate_pharmacy_register
BEFORE UPDATE OR DELETE ON pharmacy_controlled_register
FOR EACH ROW EXECUTE FUNCTION fn_prohibit_register_mutation();

CREATE TRIGGER trg_no_truncate_pharmacy_register
BEFORE TRUNCATE ON pharmacy_controlled_register
FOR EACH STATEMENT EXECUTE FUNCTION fn_prohibit_truncate();

CREATE TABLE batch_recalls (
    recall_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES catalog_items(item_id),
    batch_lot_number VARCHAR(64) NOT NULL,
    reason TEXT NOT NULL,
    recalled_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (item_id, batch_lot_number)
);

CREATE OR REPLACE PROCEDURE sp_execute_batch_recall(
    p_item_id UUID,
    p_batch_lot_number VARCHAR,
    p_reason TEXT
) LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO batch_recalls (item_id, batch_lot_number, reason)
    VALUES (p_item_id, p_batch_lot_number, p_reason)
    ON CONFLICT (item_id, batch_lot_number) DO UPDATE SET reason = EXCLUDED.reason;

    -- Immediately quarantine all lots of the recalled batch across every site/ward
    UPDATE inventory_temporal_lots
    SET lifecycle_state = 'QUARANTINED'
    WHERE item_id = p_item_id AND batch_lot_number = p_batch_lot_number;

    -- Cancel any open active reservations on those lots and release allocated quantities
    PERFORM set_config('inventory.ledger_write', 'on', true);
    UPDATE inventory_temporal_lots l
    SET quantity_allocated = GREATEST(0, l.quantity_allocated - sub.res_sum)
    FROM (
        SELECT lot_id, SUM(reserved_base_qty) AS res_sum
        FROM stock_reservations
        WHERE item_id = p_item_id AND status = 'ACTIVE'
          AND lot_id IN (
              SELECT lot_id FROM inventory_temporal_lots
              WHERE item_id = p_item_id AND batch_lot_number = p_batch_lot_number
          )
        GROUP BY lot_id
    ) sub
    WHERE l.lot_id = sub.lot_id;
    PERFORM set_config('inventory.ledger_write', 'off', true);

    UPDATE stock_reservations
    SET status = 'RELEASED'
    WHERE item_id = p_item_id AND status = 'ACTIVE'
      AND lot_id IN (
          SELECT lot_id FROM inventory_temporal_lots
          WHERE item_id = p_item_id AND batch_lot_number = p_batch_lot_number
      );
END;
$$;

-- 4. Auto Parts & Hardware OEM / Aftermarket Interchange Graph
CREATE TABLE part_interchanges (
    from_item_id UUID NOT NULL REFERENCES catalog_items(item_id) ON DELETE CASCADE,
    to_item_id UUID NOT NULL REFERENCES catalog_items(item_id) ON DELETE CASCADE,
    relation_type VARCHAR(28) NOT NULL CHECK (
        relation_type IN ('SUPERSEDED_OEM', 'AFTERMARKET_EQUIVALENT')
    ),
    PRIMARY KEY (from_item_id, to_item_id),
    CHECK (from_item_id <> to_item_id)
);

-- 5. Equipment, Scaffolding & Tool Hire Non-Consumable Asset Pool
CREATE TABLE rental_assets (
    asset_tag VARCHAR(64) PRIMARY KEY,
    item_id UUID NOT NULL REFERENCES catalog_items(item_id),
    daily_rate NUMERIC(14, 2) NOT NULL CHECK (daily_rate >= 0),
    security_deposit NUMERIC(14, 2) NOT NULL CHECK (security_deposit >= 0),
    maintenance_interval_days INT NOT NULL DEFAULT 90 CHECK (maintenance_interval_days > 0),
    cumulative_rental_days INT NOT NULL DEFAULT 0 CHECK (cumulative_rental_days >= 0),
    asset_state VARCHAR(24) NOT NULL DEFAULT 'AVAILABLE' CHECK (
        asset_state IN ('AVAILABLE', 'RENTED_OUT', 'MAINTENANCE_LOCKED', 'RETIRED')
    ),
    active_contract_id VARCHAR(64),
    checked_out_at TIMESTAMP WITH TIME ZONE,
    due_back_at TIMESTAMP WITH TIME ZONE
);

-- 6. Offline Mobile Sync Conflict Review Queue
CREATE TABLE offline_sync_conflicts (
    conflict_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key VARCHAR(128) NOT NULL,
    device_id VARCHAR(64) NOT NULL,
    sku VARCHAR(64) NOT NULL,
    lot_id UUID,
    requested_signed_qty NUMERIC(16, 6) NOT NULL,
    conflict_reason TEXT NOT NULL,
    resolution_status VARCHAR(24) NOT NULL DEFAULT 'SYNC_CONFLICT_REVIEW' CHECK (
        resolution_status IN ('SYNC_CONFLICT_REVIEW', 'REALLOCATED_RESOLVED', 'REJECTED_VOID')
    ),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
