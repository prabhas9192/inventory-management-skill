# Rule: Multi-UOM Conversion, Packaging & BOQ Recipes

Vendors sell in bulk (`Metric Tons`, `Pallets`, `Drums`), warehouses count in packages (`50kg Bags`, `Boxes`, `Vials`), and sites/kitchens consume in atomic or recipe units (`kg`, `mL`, `m³ Concrete`). Storing mixed units in a single column corrupts inventory math.

---

## Core Architecture: Canonical Base Atomic UOM

1. **Single Base UOM per SKU:** Every item defines one indivisible canonical `base_uom` (`KG`, `LITER`, `METER`, `PIECE`) in `catalog_items`. All ledger movements and `quantity_on_hand` columns are stored **exclusively in `base_uom`** using fixed-precision `NUMERIC(16, 6)` / `Decimal` (never IEEE-754 `float` for ledger balances).
2. **Conversion Factor Table (`uom_conversions`):** Maps external packaging or purchasing units to `base_uom`:
   - `1 MT` (Metric Ton) $= 1000.000000 \text{ KG}$
   - `1 BAG_50KG` $= 50.000000 \text{ KG}$
   - `1 PALLET_CEMENT` $= 40 \text{ BAG\_50KG} = 2000.000000 \text{ KG}$
3. **BOQ / BOM Recipe Explosion:** Links composite output units (e.g., `1 m³ M25 Grade Concrete`) to component material requirements in `base_uom` plus standard wastage allowance:

$$\text{Required Base Qty}_i = \text{Target Output Qty} \times \text{Recipe Norm}_i \times \left(1 + \frac{\text{Allowable Wastage \%}_i}{100}\right)$$

---

## Example: M25 Structural Concrete (`1 m³` Recipe)

| Component SKU | Base UOM | Design Mix Norm / `m³` | Allowable Wastage | Total Requisition / `m³` | Yard Issue Equivalent |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `CEMT-OPC-53` | `KG` | `380 KG` | `2.0%` | `387.6 KG` | `7.752 Bags (50kg)` |
| `AGGR-SAND-RIVER` | `KG` | `680 KG` | `4.0%` | `707.2 KG` | `0.707 MT` |
| `AGGR-STONE-20MM` | `KG` | `1250 KG` | `3.0%` | `1287.5 KG` | `1.288 MT` |
| `CHEM-PLASTICIZER` | `LITER` | `3.2 L` | `1.0%` | `3.232 L` | `3.232 Liters` |
