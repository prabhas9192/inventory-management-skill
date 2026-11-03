# Rule: The 5 Temporal Archetypes

Every item cataloged in an enterprise inventory system must be assigned to one of the 5 archetypes below.

---

## Incorrect vs. Correct Patterns

### ❌ Incorrect: Monolithic Uniform Inventory
```python
# BROKEN: Treats all inventory with a single generic "expiry_date" or assumes static value.
class Item:
    def __init__(self, name, price, expiry_date=None):
        self.name = name
        self.price = price
        self.expiry_date = expiry_date  # Misses aging goods, peak windows, and inert bulk!
```

### ✅ Correct: Archetype-Driven Temporal Modeling
```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class TemporalArchetype(Enum):
    TYPE_1_RAPID_SPOIL = "RAPID_SPOIL"   # Milk, Ready-Mix Concrete, Seafood (<7 days)
    TYPE_2_SHELF_DECAY = "SHELF_DECAY"   # Portland Cement, Antibiotics, Admixtures (30d - 2y)
    TYPE_3_MATURING    = "MATURING"      # Aged Basmati Rice, Whiskey, Cured Hardwood
    TYPE_4_BELL_CURVE  = "BELL_CURVE"    # Vintage Wine, Artisanal Cheese, Dry-Aged Beef
    TYPE_5_INERT       = "INERT"         # River Sand, Aggregates, Salt, Steel Fasteners

@dataclass
class CatalogItem:
    sku: str
    name: str
    archetype: TemporalArchetype
    base_cost: float
    base_selling_price: float
    shelf_life_days: Optional[int] = None
    maturation_days: int = 0
    peak_quality_days: int = 0
    appreciation_multiplier: float = 1.0
```

---

## Detailed Archetype Specifications

| Archetype | Quality Curve $Q(t)$ | Value Curve $V(t)$ | Storage Guardrail |
| :--- | :--- | :--- | :--- |
| **Type 1: Rapid Spoilage** | Steep drop; half-life $< 7$ days | Step-down markdown to avoid total waste | Real-time refrigeration monitoring |
| **Type 2: Shelf Decay** | Monotonic wear-out (Weibull / exponential) | Gradual discount as expiry approaches | Moisture & humidity control |
| **Type 3: Maturation** | Saturating growth curve (Von Bertalanffy) | Continuous appreciation up to target maturity | Locked in `AGING_LOCKED` status |
| **Type 4: Bell Curve** | Asymmetric Gaussian curve (Growth $\to$ Peak $\to$ Decay) | Maximum at $t_{\text{peak}}$; markdown post-peak | Climate-controlled cellar/cave |
| **Type 5: Inert** | Constant horizontal line | Constant nominal price | Open yard / bulk pile |
