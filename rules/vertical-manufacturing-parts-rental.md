# Rule: Multi-Level Kitchen/Factory BOM, Auto-Parts Interchange, Rental Assets, Tax/FX & Offline Sync

Covers the remaining operational verticals implemented in `engine/bom_parts_rental_sync.py`.

---

## 1. Restaurant, Bakery & Manufacturing Multi-Level BOM (`produce_prep_or_subassembly_batch`)

- Single-level recipes fail when kitchens or factories prepare intermediate sub-batches (e.g., raw tomatoes $\to$ `10 L` pizza sauce prep batch $\to$ finished pizza, or raw potatoes peeled at `82%` yield).
- Always issue raw lots via `ISSUE_OUT`, apply `yield_pct` to calculate net output quantity, and capitalize `(sum(consumed_ingredient_cost) + direct_conversion_cost) / output_qty` into the intermediate lot's `RECEIPT_GRN` landed unit cost.

---

## 2. Auto Parts, Hardware & Industrial Spares (`PartInterchangeGraph` & `CoreDepositTracker`)

- **Supersession & Equivalent Graph:** Distinguish directed **OEM supersessions** (`OLD-OEM -> NEW-OEM`) from bidirectional **aftermarket equivalents** (`BOSCH <-> DENSO`). Resolve stockouts in strict rank order: `EXACT` $\to$ `SUPERSEDED_OEM` $\to$ `AFTERMARKET_EQUIVALENT`.
- **Core-Return Deposits:** Track refundable core charges on batteries, alternators, and gas cylinders separately from product revenue; refund deposit only upon physical dirty-core check-in.

---

## 3. Equipment, Scaffolding & Tool Hire (`RentalAssetPool`)

- Rental inventory is **non-consumable**: never use `ISSUE_OUT` to permanently destroy stock on rental dispatch.
- Cycle each tagged asset through `AVAILABLE` $\to$ `RENTED_OUT` $\to$ `AVAILABLE` / `MAINTENANCE_LOCKED`, billing `regular_days * daily_rate + overdue_days * late_multiplier * daily_rate`, deducting `damage_fee` from `security_deposit`, and auto-locking the asset once `cumulative_rental_days >= maintenance_interval_days`.

---

## 4. Multi-Currency & GST/VAT Tax-Stripped Landed Cost (`compute_multicurrency_landed_cost`)

- Per **IAS 2 / Ind AS 2**, **recoverable input GST/VAT must NEVER be capitalized into inventory cost** (book it to Input Tax Credit Receivable).
- Capitalize `foreign_price * spot_fx + customs_duty + non_recoverable_taxes + freight_and_insurance` divided by `accepted_base_qty`.

---

## 5. Offline Field Sync Conflict Resolution (`replay_offline_sync_batch`)

- Disconnected construction sites, delivery vans, and POS lanes queue events with `(client_timestamp, seq, idempotency_key)`.
- Replay in causal order; if two offline devices depleted the same lot while disconnected, commit the valid movement and isolate the over-drawn event into `SYNC_CONFLICT_REVIEW` for supervisor bin reallocation—never drop events silently or allow negative stock.
