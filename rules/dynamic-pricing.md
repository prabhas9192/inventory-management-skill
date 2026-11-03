# Rule: Dynamic Pricing & Valuation

Inventory value is dynamic. Systems must compute price adjustments reflecting degradation or maturation.

---

## 1. Dynamic Markdown Optimization (Type 1 & 2)

As perishable goods approach expiration, static pricing results in catastrophic zero-revenue waste. Systems must trigger automated markdowns:

$$\text{Discount Rate} = \min\left(0.50, \left(1 - \frac{\text{Days Remaining}}{\text{Warning Threshold Days}}\right) \times 0.50\right)$$

* Caps markdown at 50% to guarantee baseline variable cost recovery.
* Automatically updates Electronic Shelf Labels (ESL) or POS terminal prices.

---

## 2. Maturation Vintage Appreciation Markup (Type 3)

For items that gain value with time (Aged Basmati Rice, Cask Whiskey, Seasoned Timber):

$$\text{Price}(t) = \begin{cases} 
\text{Base Price} \quad (\text{LOCKED: Cannot be sold}), & t < t_{\text{mature}} \\
\text{Base Price} \times \left(1 + (\text{Multiplier} - 1) \times \left(0.5 + 0.5 \times \frac{t - t_{\text{mature}}}{t_{\text{mature}}}\right)\right), & t \ge t_{\text{mature}}
\end{cases}$$

### Example Valuation Benchmarks
* **Un-aged Basmati Rice (90 Days):** Base Cost: \$1.80, Base Price: \$3.50. Status: `AGING_LOCKED`.
* **Fully Aged Basmati Rice (450 Days):** Base Cost: \$1.80, Selling Price: **\$5.12** (+46% premium markup).
