---
name: inventory-management
description: >-
  Architect, model, audit, and implement enterprise inventory management systems across all industry domains:
  construction yards, supermarkets/POS, apparel/fashion variants, serialized electronics/IMEI, regulated
  pharmacies/hospitals, restaurants/manufacturing BOMs, auto parts, and equipment rental. Covers the 5 Temporal
  Archetypes, FEFO/FMFO/Peak-Window/LIFO/JIT dispatch, immutable row-locked stock ledgers, multi-UOM, weighbridge
  GRNs, Moving Weighted Average COGS, multi-currency IAS-2 landed cost, and offline sync conflict resolution.
metadata:
  icon: inventory_2
---

# Universal Enterprise Inventory Management & Multi-Vertical ERP Skill

A complete, zero-dependency framework for architecting and deploying inventory systems across **construction, supermarkets, apparel/footwear, serialized electronics, regulated pharmacies/hospitals, restaurants/manufacturing, auto parts, and equipment hire**.

---

## Core Invariants (Always Enforced)

1. **Assign every SKU to one of the 5 Temporal Archetypes:** `RAPID_SPOIL`, `SHELF_DECAY`, `MATURING`, `BELL_CURVE`, or `INERT`, with configurable `decay_model` and `maturation_curve`.
2. **Hard-lock maturing & expired/recalled stock:** Prohibit premature sales of aging goods (`AGING_LOCKED`), lock post-peak spoiled bell-curve items (`EXPIRED_LOCKED`), and freeze recalled batches across all locations.
3. **Route dispatches by archetype + enforce statutory batch MRP ceilings:** `FEFO` + markdowns for decaying stock, `FMFO` + vintage markups for aging stock, `PEAK_WINDOW` for bell-curve stock, `LIFO` for inert bulk piles, and clamp retail prices at `printed_mrp`.
4. **Never overwrite `quantity_on_hand` outside the immutable ledger:** All stock movements must go through append-only `stock_ledger_entries` with `FOR UPDATE` row locks, composite `(lot_id, item_id)` FKs, strict movement-sign checks, and payload-verified `idempotency_key` deduplication.
5. **Use canonical `base_uom` and exact `Decimal` / `NUMERIC` everywhere:** Convert purchasing, stocking, scale-barcode, and multi-level BOM recipe units into atomic `base_uom`; strip recoverable GST/VAT from IAS-2 landed cost.
6. **Decouple physical picking from financial COGS:** Value outbound issues at **Moving Weighted Average Cost (MWAC)** (and site returns at original issue cost snapshot) regardless of physical `LIFO` / `FEFO` / `FMFO` picking.
7. **Activate vertical compliance modules where required:** Style–Color–Size matrices + size-broken clearance for apparel; 15-digit Luhn IMEI + dual warranty + RMA swaps for electronics; GS1 DataMatrix + dual-witness SHA-256 narcotics register + BUD shortening for pharmacy; non-consumable check-out/check-in loops for rental assets.

---

## Directory Reference Structure

### Core Temporal & ERP Rules
- **[rules/temporal-archetypes.md](./rules/temporal-archetypes.md)** — Specifications for the 5 archetypes and quality trajectories.
- **[rules/dispatch-policies.md](./rules/dispatch-policies.md)** — Routing rules for FEFO, FMFO, Peak-Window, LIFO, reservations, and JIT.
- **[rules/dynamic-pricing.md](./rules/dynamic-pricing.md)** — Dynamic markdown optimization vs. vintage maturation appreciation.
- **[rules/perishable-safety.md](./rules/perishable-safety.md)** — Cold-chain MKT checks, 4-tier liquidation cascade, and barcode locks.
- **[rules/ledger-and-concurrency.md](./rules/ledger-and-concurrency.md)** — Append-only stock ledger, `FOR UPDATE` lock ordering, and idempotency keys.
- **[rules/uom-and-packaging.md](./rules/uom-and-packaging.md)** — Canonical `base_uom`, packaging multipliers, and BOQ recipe explosion.
- **[rules/grn-cycle-counts-and-wastage.md](./rules/grn-cycle-counts-and-wastage.md)** — Weighbridge gross/tare/moisture GRNs, blind cycle counts, and WBS BOQ wastage variance.
- **[rules/cost-accounting-cogs.md](./rules/cost-accounting-cogs.md)** — Landed freight allocation and Moving Weighted Average Cost (`MWAC`).

### Industry Vertical Rules
- **[rules/vertical-retail-apparel-electronics.md](./rules/vertical-retail-apparel-electronics.md)** — EAN-13 scale barcodes, batch MRP, bundles/BOGO, Style–Color–Size matrices, size-brokenness, IMEI Luhn, dual warranties, and RMA swaps.
- **[rules/vertical-pharmacy-compliance.md](./rules/vertical-pharmacy-compliance.md)** — GS1 DataMatrix `(01)(17)(10)(21)`, Schedule H/H1/Narcotics dual-witness SHA-256 register, batch recall freeze, open-vial BUD, and cold-chain quarantine.
- **[rules/vertical-manufacturing-parts-rental.md](./rules/vertical-manufacturing-parts-rental.md)** — Multi-level BOM with yield loss, OEM/aftermarket interchange, core deposits, equipment rental loops, multi-currency GST/VAT landed cost, and offline sync conflict resolution.

### Schemas, Math, Engine Modules & Test Suite
- **[references/database-schemas.sql](./references/database-schemas.sql)** — PostgreSQL 16+ verified DDL across core ledger, reservations, transfers, variants, serials/IMEI, pharmacy register, batch recalls, part interchanges, rental assets, and offline sync conflicts.
- **[references/mathematical-models.md](./references/mathematical-models.md)** — Weibull, Arrhenius MKT, cement strength retention, and Zanoni EOQ-M equations.
- **[scripts/engine/](./scripts/engine/)** — Modular pure-Python engine (`core.py`, `erp_ledger.py`, `replenishment.py`, `retail_variants_serials.py`, `pharmacy_compliance.py`, `bom_parts_rental_sync.py`).
- **[scripts/evaluate-stock.py](./scripts/evaluate-stock.py)**, **[scripts/dispatch-order.py](./scripts/dispatch-order.py)**, **[scripts/reconcile-ledger.py](./scripts/reconcile-ledger.py)** — Standalone CLI runners.
- **[tests/test_inventory_skill.py](./tests/test_inventory_skill.py)** — Automated unit, concurrency, and multi-vertical test suite.

---

## Verification Command

```bash
python3 -B -m unittest discover -s tests -v
```
