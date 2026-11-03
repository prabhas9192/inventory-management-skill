# Rule: Retail Supermarket, Apparel Variants & Serialized Electronics

When building inventory for supermarkets, fashion/footwear, or electronics/appliances, activate the vertical extensions in `engine/retail_variants_serials.py`.

---

## 1. Supermarket / Kirana / FMCG Extensions

1. **Variable-Weight & Price-Embedded Scale Barcodes (`20–23` EAN-13):**
   - Delis, produce counters, and sweet shops print 13-digit barcodes (`PP IIIII VVVVV C`) where `IIIII` is the 5-digit PLU and `VVVVV` encodes weight in grams (`20`/`22`) or price (`21`/`23`).
   - Always validate the Modulo-10 check digit via `parse_scale_ean13()` before deducting weight from the bulk produce lot.
2. **Batch-Wise Statutory MRP Ceiling (`printed_mrp`):**
   - Two on-hand batches of the same packaged SKU frequently carry different printed MRPs. Store `printed_mrp` on `inventory_temporal_lots` (not just `catalog_items`), and clamp dynamic selling price via `enforce_mrp_ceiling(price, lot.printed_mrp)`.
3. **Bundles & BOGO Promotions:**
   - Virtual combo packs compute sellable stock dynamically from the bottleneck component (`compute_bundle_availability()`).
   - BOGO (`compute_bogo_quantities()`) deducts `paid_units + free_promo_units` from the stock ledger while booking revenue on `paid_units` so physical stock never drifts from POS promos.

---

## 2. Apparel, Footwear & Furniture Variant Matrix

1. **Parent Style $\to$ Cartesian Variant SKUs (`generate_variant_matrix`):**
   - Never ask store operators to create 30 unrelated shirt items manually. Define one `parent_sku` (`SHIRT-402`) and expand across `attribute_axes` (`color × size × fit`) with optional size surcharges (`XXL`).
2. **Size-Curve Health & "Size-Broken" Clearance (`evaluate_size_curve_health`):**
   - Total stock quantity is misleading in fashion: 40 shirts left in `XS` and `XXXL` with zero `M` or `L` is **size-broken dead stock**.
   - Compare remaining size availability against `target_weights`; once core sizes stock out (`completeness < 60%`), trigger automatic broken-assortment clearance markdowns.
3. **Customer Trial / Return Grading (`grade_customer_return`):**
   - Route inspected returns explicitly: `GRADE_A_NEW` $\to$ sellable lot, `GRADE_B_OPEN_BOX` $\to$ markdown clearance lot, `GRADE_C_DEFECTIVE` $\to$ `QUARANTINED` vendor claim bin.

---

## 3. Electronics, Mobiles & Machinery (`1 Unit = 1 Serial / IMEI`)

1. **Per-Unit Serial Registry (`SerializedUnitRegistry`):**
   - Lot quantities alone are insufficient for phones, laptops, batteries, or power tools. Every unit carries a unique `serial_number` (validated by 15-digit GSMA Luhn checksum `validate_imei_luhn()` when `is_imei=True`).
2. **Dual Warranty Windows:**
   - Track **Vendor Warranty** (starts at `grn_date`) separately from **Customer Warranty** (starts at `sold_date`). On RMA intake (`open_rma()`), check both windows to determine whether customer repair is free and whether vendor back-to-back recovery applies.
3. **RMA Swap Integrity (`resolve_rma_with_swap`):**
   - Swapping a defective unit transitions the faulty serial to `REPLACED_CLOSED` and binds the replacement `IN_STOCK` serial to the customer's original invoice/warranty window.
