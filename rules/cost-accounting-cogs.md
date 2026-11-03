# Rule: Decoupling Physical Picking from Financial COGS Valuation

**Physical picking order and financial Cost of Goods Sold (COGS) valuation are two separate concerns.** Mixing them causes broken tax accounting (e.g., IFRS prohibits LIFO for financial reporting, even when a wheel loader physically scoops river sand `LIFO` from the top of an open pile).

---

## Separation Matrix

| Dimension | Governs | Allowed Methods | Example (River Sand Pit) |
| :--- | :--- | :--- | :--- |
| **Physical Dispatch** | Which physical batch/bin leaves the yard | `FEFO`, `FMFO`, `PEAK_WINDOW`, `LIFO`, `FIFO` | `LIFO` (scoop newest dump from top of pit) |
| **Financial Accounting** | Unit cost booked to COGS / Project WIP | `Moving Weighted Average (MWAC)`, `FIFO Cost Layer`, `Specific Lot Cost` | `MWAC` (IFRS / GAAP compliant project costing) |

---

## 1. Landed Unit Cost on Inbound GRN

Purchase price alone understates material cost. Allocate freight, crane unloading, customs, and transit insurance across accepted GRN lines (by weight for bulk materials, by value for high-value equipment/pharma):

$$C_{\text{landed, unit}} = \frac{(Q_{\text{accepted}} \times P_{\text{vendor, unit}}) + \text{Allocated Freight} + \text{Unloading} + \text{Non-Recoverable Duties}}{Q_{\text{accepted}}}$$

---

## 2. Moving Weighted Average Cost (MWAC) Formula

Recalculated atomically **only on positive inbound cost-bearing receipts (`RECEIPT_GRN`)**:

$$\text{MWAC}_{\text{new}} = \frac{(Q_{\text{on\_hand}} \times \text{MWAC}_{\text{current}}) + (Q_{\text{accepted}} \times C_{\text{landed, unit}})}{Q_{\text{on\_hand}} + Q_{\text{accepted}}}$$

* **Outbound Issues (`ISSUE_OUT`):** Booked to COGS / WBS Project Cost at $\text{MWAC}_{\text{current}}$ (does **not** change $\text{MWAC}_{\text{current}}$).
* **Site Returns (`RETURN_FROM_SITE`):** Re-entered at the original issue's `unit_cost_snapshot`.
* **Maturing / High-Value Serialized Lots (Aged Whiskey, Vintage Wine):** Use **Specific Lot Identification Cost** (`lot_landed_cost + accumulated_cellar_holding_cost`) so margin reflects exact vintage economics.
