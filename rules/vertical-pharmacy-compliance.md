# Rule: Pharmacy, Hospital & Cold-Chain Regulatory Compliance

When building pharmacy, hospital ward (ADC/Pyxis), blood bank, or clinical reagent inventory, enforce all 6 statutory controls implemented in `engine/pharmacy_compliance.py`.

---

## Mandatory Clinical & Statutory Controls

1. **GS1 DataMatrix Pack Scanning (`parse_gs1_datamatrix`):**
   - Extract AI `(01)` GTIN-14 (with Modulo-10 checksum verification), `(17)` Expiry Date (`YYMMDD`, honoring the GS1 `DD=00` last-day-of-month rule), `(10)` Batch/Lot, and `(21)` Unique Pack Serial directly from 2D scans—prohibit manual expiry typing when a barcode is present.
2. **Schedule H / H1 & Narcotics (`NARCOTIC_SCHEDULE_X`) Perpetual Register (`ControlledSubstanceRegister`):**
   - `SCHEDULE_H`, `SCHEDULE_H1`, and `NARCOTIC_SCHEDULE_X` dispenses **must** record `patient_id`, `prescription_id`, and `prescriber_reg_no`.
   - `NARCOTIC_SCHEDULE_X` dispenses and wastage destructions **must** enforce a **distinct second-person `witness_id`** (`witness_id != actor_id`).
   - Chain every register row with SHA-256 (`prev_hash -> entry_hash`) and verify via `verify_chain_integrity()`.
3. **Instant Multi-Location Batch Recall Freeze (`trigger_batch_recall`):**
   - Issuing a recall on `(sku, batch_number)` immediately blocks any further dispense of that batch across all wards/stores **and** returns a full patient/prescription trace report of every dose previously dispensed from that batch.
4. **Open-Vial / Reconstituted Beyond-Use-Date (`compute_open_container_bud`):**
   - Once a multi-dose insulin vial, eye drop, or reconstituted antibiotic is opened at `opened_at`, override its effective expiry with $\min(\text{Manufacturer Expiry}, \text{opened\_at} + \text{in\_use\_stability\_hours})$.
5. **Automated Cold-Chain Freeze & MKT Quarantine (`evaluate_cold_chain_excursion`):**
   - Biologics and vaccines spoil both from heat (`MKT > 8°C`) **and** accidental freezing (`<= 0°C` for adjuvant vaccines/insulin). Automatically transition breached lots to `QUARANTINED`.
6. **Near-Expiry Distributor Return Claims (`build_near_expiry_return_claims`):**
   - Surface slow-moving batches inside `[distributor_cutoff_days, return_window_days]` (e.g., 15–90 days before expiry) to generate supplier credit notes before they cross the non-returnable deadline.
