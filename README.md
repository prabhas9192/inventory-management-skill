# Universal Enterprise Inventory Management Skill

A production-grade, zero-dependency AI coding skill, domain engine, and PostgreSQL 16 schema for architecting and implementing inventory management systems across every major industry vertical.

---

## Supported Industry Verticals

| Domain | Core Capabilities |
| :--- | :--- |
| **Construction Yard, Bulk, Cold Store, Grain & Wine** | 5 Temporal Archetypes (`RAPID_SPOIL`, `SHELF_DECAY`, `MATURING`, `BELL_CURVE`, `INERT`), `FEFO` / `FMFO` / `PEAK_WINDOW` / `LIFO` + `JIT` pour scheduling, weighbridge GRNs with moisture cut, WBS BOQ wastage variance, Moving Weighted Average Cost (`MWAC`), `ROP` / `EOQ` replenishment, and 3-way PO matching |
| **Supermarket, Grocery & POS** | Variable-weight and price-embedded EAN-13 scale PLU barcodes (`02` / `20`–`29` with Modulo-10 check digit), statutory batch-level `printed_mrp` ceiling clamp, virtual bundles, and BOGO physical stock deduction |
| **Apparel, Footwear & Fashion** | Parent Style → Color/Size/Attribute Cartesian matrix generator, core size-curve health & size-broken clearance detector, and customer return grading (`GRADE_A_NEW`, `GRADE_B_OPEN_BOX`, `GRADE_C_DEFECTIVE`) |
| **Electronics, Mobiles & Machinery** | `1 unit = 1 unique Serial / IMEI` with 15-digit GSMA Luhn validator, dual vendor + customer warranty tracking, and full RMA intake/swap lifecycle |
| **Pharmacy & Hospital Medications** | GS1 DataMatrix `(01)(17)(10)(21)` parser (`DD=00` end-of-month rule), Schedule H/H1 & Narcotics perpetual register with mandatory distinct dual-witness sign-off and SHA-256 hash chain, multi-location atomic batch recall freeze + patient trace, open-vial `BUD` shortening, cold-chain MKT/freeze quarantine, and near-expiry vendor return claims |
| **Restaurant, Bakery & Manufacturing** | Multi-level prep/subassembly BOM explosion with processing/peeling `yield_pct` and rolled-up ingredient + conversion cost into `MWAC` |
| **Auto Parts & Hardware** | Directed `OEM_SUPERSEDES` + bidirectional `AFTERMARKET_EQUIVALENT` graph and refundable dirty-core deposit ledger |
| **Equipment & Tool Rental** | Non-consumable check-out/check-in asset loop, daily/overdue billing, damage deposit settlement, and cumulative-day `MAINTENANCE_LOCKED` safety hold |
| **Cross-Cutting Enterprise** | Multi-currency IAS-2 / Ind AS 2 landed cost stripping recoverable input GST/VAT, and offline field/POS sync replay with `SYNC_CONFLICT_REVIEW` isolation |

---

## Repository Structure

```text
inventory-management-skill/
├── SKILL.md                           # Main skill specification & core invariants
├── examples/
│   └── sample-catalog.json            # Sample multi-archetype SKU & lot dataset
├── references/
│   ├── database-schemas.sql           # Verified PostgreSQL 16+ DDL, triggers & stored procedures
│   └── mathematical-models.md         # Weibull, Arrhenius MKT, cement strength & Zanoni EOQ-M math
├── rules/                             # 11 modular domain & compliance rulebooks
│   ├── cost-accounting-cogs.md
│   ├── dispatch-policies.md
│   ├── dynamic-pricing.md
│   ├── grn-cycle-counts-and-wastage.md
│   ├── ledger-and-concurrency.md
│   ├── perishable-safety.md
│   ├── temporal-archetypes.md
│   ├── uom-and-packaging.md
│   ├── vertical-manufacturing-parts-rental.md
│   ├── vertical-pharmacy-compliance.md
│   └── vertical-retail-apparel-electronics.md
├── scripts/                           # Pure-Python 3.10+ engine (zero third-party dependencies)
│   ├── evaluate-stock.py              # CLI: evaluate lot lifecycle, quality & dynamic pricing
│   ├── dispatch-order.py              # CLI: simulate FEFO/FMFO/Peak-Window/LIFO dispatch
│   ├── reconcile-ledger.py            # CLI: demo GRN, landed cost, MWAC COGS & BOQ variance
│   └── engine/
│       ├── __init__.py
│       ├── core.py
│       ├── erp_ledger.py
│       ├── replenishment.py
│       ├── retail_variants_serials.py
│       ├── pharmacy_compliance.py
│       └── bom_parts_rental_sync.py
└── tests/
    ├── test_inventory_skill.py        # 21 automated Python unit, concurrency & vertical tests
    └── sql/
        ├── test_ledger_immutability.sql
        └── test_vertical_domains.sql
```

---

## Installation as an AI Agent Skill

### Google Antigravity (Global Skill)
```bash
mkdir -p ~/.gemini/config/skills/inventory-management
cp -r SKILL.md rules references scripts examples tests ~/.gemini/config/skills/inventory-management/
```

### Workspace-Scoped Skill (Antigravity / Cursor / Claude / Codex)
Copy this folder into `<your-project>/.agents/skills/inventory-management/` (or `.agent/skills/inventory-management/`).

---

## Quick Verification & CLI Usage

Run the full automated Python test suite (no external packages required):

```bash
python3 -B -m unittest discover -s tests -v
```

Run the standalone CLI tools:

```bash
python3 scripts/evaluate-stock.py --input examples/sample-catalog.json
python3 scripts/dispatch-order.py --input examples/sample-catalog.json --sku MILK-PAST-1L --qty 150
python3 scripts/reconcile-ledger.py
```

Load and verify the PostgreSQL 16 schema:

```bash
psql -U postgres -v ON_ERROR_STOP=1 -f references/database-schemas.sql
```
