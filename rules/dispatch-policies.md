# Rule: Multi-Strategy Dispatch Policies

Enterprise stock fulfillment must route to the mathematically optimal picking strategy based on the item's archetype.

---

## Strategy Matrix

```
┌─────────────────────────┬─────────────────────────┬─────────────────────────┐
│ ITEM ARCHETYPE          │ DISPATCH STRATEGY       │ PICKING CRITERIA        │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ Type 1: Rapid Spoilage  │ FEFO                    │ Min days to expiry      │
│ Type 2: Shelf Decay     │ FEFO                    │ Min days to expiry      │
│ Type 3: Maturation      │ FMFO (First-Matured)    │ Oldest mature lot       │
│ Type 4: Bell Curve      │ Peak-Window Selection   │ Max quality score       │
│ Type 5: Inert           │ LIFO (Open Piles)       │ Newest batch on top     │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

---

## Incorrect vs. Correct Implementation

### ❌ Incorrect: Naive FIFO Everywhere
```python
# BROKEN: Standard FIFO sells green un-aged rice first, and wastes money scooping sand from bottom of pile.
def pick_stock(lots, qty):
    lots.sort(key=lambda x: x.mfg_date) # Rigid FIFO
    # Disasters:
    # 1. Picks un-aged rice that should have matured.
    # 2. Ignores actual expiry dates on medicine/milk.
    # 3. Requires bulldozing the entire sand pile to get to the oldest grain!
```

### ✅ Correct: Archetype-Aware Dispatch Routing
```python
def get_dispatch_queue(lots, current_date):
    archetype = lots[0].item.archetype
    
    if archetype in (TemporalArchetype.TYPE_1_RAPID_SPOIL, TemporalArchetype.TYPE_2_SHELF_DECAY):
        # FEFO: Filter expired; sort by expiry ascending
        valid = [l for l in lots if l.days_to_expiry(current_date) > 0]
        return sorted(valid, key=lambda l: l.days_to_expiry(current_date))
        
    elif archetype == TemporalArchetype.TYPE_3_MATURING:
        # FMFO: Filter un-mature; sort by age descending (oldest mature first)
        valid = [l for l in lots if l.age_days(current_date) >= l.item.maturation_days]
        return sorted(valid, key=lambda l: l.age_days(current_date), reverse=True)
        
    elif archetype == TemporalArchetype.TYPE_5_INERT:
        # LIFO: Pick newest batch (top of pile) to minimize yard equipment handling
        return sorted(lots, key=lambda l: l.mfg_date, reverse=True)
```
