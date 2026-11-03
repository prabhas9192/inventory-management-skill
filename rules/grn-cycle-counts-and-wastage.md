# Rule: GRN Weighbridge Receiving, Blind Cycle Counts & BOQ Wastage Audits

Physical reality diverges from purchase orders: trucks arrive short, wet sand carries excess water weight, cement bags burst in transit, and site materials experience shrinkage or pilferage.

---

## 1. Goods Received Note (GRN) & Weighbridge Formula

Never credit stock directly from the Vendor Invoice or Purchase Order quantity. Every inbound delivery records five distinct quantities:

1. `po_ordered_qty` — Quantity requested on Purchase Order.
2. `challan_billed_qty` — Quantity claimed on vendor delivery note.
3. `weighbridge_net_qty` — Measured physical mass adjusted for moisture:
   $$\text{Net Dry Mass} = (\text{Gross Truck Weight} - \text{Empty Tare Weight}) \times \left(1 - \frac{\max(0, \text{Measured Moisture \%} - \text{Standard Moisture \%})}{100}\right)$$
4. `qc_rejected_qty` — Damaged bags, failed slump/cube samples, or temperature-breached vials.
5. `accepted_stock_qty` — **Only this quantity enters `RECEIPT_GRN` on the stock ledger:**
   $$\text{Accepted Qty} = \text{Net Dry Mass (or Physical Count)} - \text{QC Rejected Qty}$$

If $|\text{Challan Qty} - \text{Weighbridge Net Qty}| / \text{Challan Qty} > \text{Tolerance \%}$ (typically `0.5%` for steel/cement, `1.5%` for bulk aggregates), generate a **Vendor Short-Supply Debit Note** automatically.

---

## 2. Blind Physical Cycle Counts

1. **Snapshot Freeze:** Capture `system_book_qty` at count initiation, but **hide `system_book_qty` from the mobile counting screen** (blind count) so warehouse staff cannot lazily type the expected number.
2. **Variance Calculation:**
   $$\Delta Q = Q_{\text{counted}} - Q_{\text{system\_book}}$$
3. **Approval Thresholds:** Minor variances within tolerance post an automatic `CYCLE_COUNT_ADJUST` ledger entry; variances exceeding value/percentage thresholds lock the bin for supervisor recount and sign-off.

---

## 3. Construction BOQ Theoretical vs. Actual Wastage Audit

At each WBS milestone (e.g., completion of 3rd Floor Slab), reconcile cumulative issued materials (`ISSUE_OUT - RETURN_FROM_SITE`) against as-built engineered volume:

$$\text{Excess Wastage \%} = \frac{Q_{\text{net\_issued}} - Q_{\text{theoretical\_BOQ}} \times (1 + w_{\text{allowed}})}{Q_{\text{theoretical\_BOQ}}} \times 100$$

* $\text{Excess Wastage \%} \le 0$: Within engineering norm.
* $\text{Excess Wastage \%} > 0$: Triggers **Over-Consumption / Pilferage Alert** against the subcontractor work package (deductible from subcontractor RA bill if contractually stipulated).
