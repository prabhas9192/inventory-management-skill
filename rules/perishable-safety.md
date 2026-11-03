# Rule: Perishable Safety & Automated Liquidation Cascade

To prevent dispensing expired pharmaceuticals or pouring bad concrete, the system mandates automated hardware and software locks.

---

## 4-Tier Automated Liquidation Cascade

```mermaid
flowchart TD
    POLL["Daily / Hourly Expiry Health Scan"]
    
    POLL --> T1{"Days to Expiry <= Warning Threshold?"}
    T1 -->|Yes| A1["Tier 1: FEFO Priority Promotion & Dynamic Markdown (15-30% off)"]
    
    POLL --> T2{"Days to Expiry <= Critical Threshold?"}
    T2 -->|Yes| A2["Tier 2: Inter-Site Cross-Docking (Transfer to high-burn mega-site)"]
    
    POLL --> T3{"Days to Expiry <= Supplier Return Deadline?"}
    T3 -->|Yes| A3["Tier 3: Vendor Return-to-Credit Claim Generated"]
    
    POLL --> T4{"Days to Expiry <= 0 Days?"}
    T4 -->|Yes| A4["Tier 4: Hard Lock (Hardware scanner rejects barcode; zero sales allowed)"]
```

---

## Invariant Safety Checks

1. **Hard Scan Rejection:** If `current_date >= lot.expiry_date`, the barcode scanner at the POS terminal, nursing station, or construction gate must emit an audible alarm and throw an unbypassable exception.
2. **Cold-Chain IoT Breach Lock:** If Mean Kinetic Temperature ($T_K$) exceeds the allowable range ($2^\circ\text{C} – 8^\circ\text{C}$ for biologics), the batch must immediately transition to `QUARANTINED` pending QA approval.
