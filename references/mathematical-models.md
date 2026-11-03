# Mathematical Inventory Kinetics & Operations Research Reference

This reference documents the governing mathematical equations used across the skill.

---

## 1. Weibull Continuous Deterioration Rate
The instantaneous deterioration rate $\theta(t)$ follows a two-parameter Weibull distribution:

$$\theta(t) = \alpha \beta t^{\beta - 1}$$

* $\alpha > 0$: Scale parameter.
* $\beta > 1$: Wear-out phase (deterioration rate increases with age, typical for cement, chemical admixtures).
* $\beta = 1$: Constant decay rate (exponential distribution, Ghare & Schrader model).

---

## 2. Arrhenius Chemical Shelf-Life Degradation
Reaction rate constant $k(T)$ as a function of temperature:

$$k(T) = A \cdot \exp\left( -\frac{E_a}{R \cdot T} \right)$$

Mean Kinetic Temperature ($T_K$):

$$T_K = \frac{\Delta H / R}{-\ln \left( \frac{1}{n} \sum_{i=1}^n \exp\left( -\frac{\Delta H}{R \cdot T_i} \right) \right)}$$

---

## 3. Portland Cement Strength Retention
Compressive strength $S(t)$ in MPa after $t$ storage days under relative humidity $RH$:

$$S(t) = S_0 \times \exp\left( -k \cdot t \cdot \left(\frac{RH}{100}\right)^2 \right)$$

Where $S_0 = 53 \text{ MPa}$ and $k \approx 0.0035 \text{ day}^{-1}$.

---

## 4. Grain & Spirit Maturation Curve
Quality score $Q(t)$ saturating toward peak vintage maturity:

$$Q(t) = Q_0 + (1.0 - Q_0) \times \left(1 - \exp(-k_{\text{aging}} \cdot t)\right)$$

For Basmati rice, $k_{\text{aging}} = 3.0 / t_{\text{mature}}$.

---

## 5. Maturing Economic Order Quantity (EOQ-M / Zanoni Model)
Maximizing profit per unit time over batch size $Q$ and maturation duration $T_m$:

$$\max \Pi(T_m, Q) = \frac{P(T_m) \cdot Q - C_{\text{order}} - \int_0^{T_m} H(t) dt}{T_m + \frac{Q}{D}}$$
