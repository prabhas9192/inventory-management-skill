"""
Replenishment, Lot-Sizing & Portfolio Analytics Engine:
- Safety Stock & Dynamic Reorder Point (ROP) under demand + lead-time variance
- Classical EOQ, Perishable/Deteriorating EOQ (Ghare-Schrader), and Maturing EOQ-M (Zanoni)
- ABC / XYZ Inventory Classification Matrix
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Sequence


Q2 = Decimal("0.01")
Q4 = Decimal("0.0001")


def _d(val: float | int | str | Decimal) -> Decimal:
    return val if isinstance(val, Decimal) else Decimal(str(val))


def safety_stock(
    std_daily_demand: float | Decimal,
    mean_lead_time_days: float | Decimal,
    mean_daily_demand: float | Decimal = 0.0,
    std_lead_time_days: float | Decimal = 0.0,
    service_level_z: float = 1.96,
) -> Decimal:
    """
    SS = Z * sqrt( L * sigma_d^2 + d_bar^2 * sigma_L^2 )
    """
    sd, ml, md, sl = float(std_daily_demand), float(mean_lead_time_days), float(mean_daily_demand), float(std_lead_time_days)
    if ml < 0 or sd < 0 or md < 0 or sl < 0 or service_level_z < 0:
        raise ValueError("Safety stock inputs must be non-negative")
    variance = (ml * (sd ** 2)) + ((md ** 2) * (sl ** 2))
    ss = service_level_z * math.sqrt(variance)
    return _d(ss).quantize(Q2, rounding=ROUND_HALF_UP)


def reorder_point(
    mean_daily_demand: float | Decimal,
    mean_lead_time_days: float | Decimal,
    std_daily_demand: float | Decimal = 0.0,
    std_lead_time_days: float | Decimal = 0.0,
    service_level_z: float = 1.96,
) -> Decimal:
    """
    ROP = (d_bar * L_bar) + Z * sqrt( L_bar * sigma_d^2 + d_bar^2 * sigma_L^2 )
    """
    lead_demand = _d(mean_daily_demand) * _d(mean_lead_time_days)
    ss = safety_stock(std_daily_demand, mean_lead_time_days, mean_daily_demand, std_lead_time_days, service_level_z)
    return (lead_demand + ss).quantize(Q2, rounding=ROUND_HALF_UP)


def eoq(
    annual_demand: float | Decimal,
    order_setup_cost: float | Decimal,
    annual_holding_cost_per_unit: float | Decimal,
) -> Decimal:
    """Classical Wilson Economic Order Quantity: sqrt(2 * D * K / h)."""
    d, k, h = float(annual_demand), float(order_setup_cost), float(annual_holding_cost_per_unit)
    if d <= 0 or k <= 0 or h <= 0:
        raise ValueError("annual_demand, order_setup_cost, and annual_holding_cost_per_unit must be > 0")
    return _d(math.sqrt((2.0 * d * k) / h)).quantize(Q2, rounding=ROUND_HALF_UP)


def eoq_perishable(
    annual_demand: float | Decimal,
    order_setup_cost: float | Decimal,
    annual_holding_cost_per_unit: float | Decimal,
    unit_purchase_cost: float | Decimal,
    annual_deterioration_rate: float | Decimal,
    max_shelf_life_years: float | Decimal | None = None,
) -> Decimal:
    """
    Ghare-Schrader / Covert deteriorating EOQ approximation:
    Effective holding rate = h + theta * c, capped by shelf-life demand ceiling D * T_max.
    """
    d, k = float(annual_demand), float(order_setup_cost)
    h, c, theta = float(annual_holding_cost_per_unit), float(unit_purchase_cost), float(annual_deterioration_rate)
    if d <= 0 or k <= 0 or h < 0 or c < 0 or theta < 0 or (h + theta * c) <= 0:
        raise ValueError("Invalid perishable EOQ parameters")
    q_star = math.sqrt((2.0 * d * k) / (h + theta * c))
    if max_shelf_life_years is not None:
        t_max = float(max_shelf_life_years)
        if t_max <= 0:
            raise ValueError("max_shelf_life_years must be > 0")
        q_star = min(q_star, d * t_max)
    return _d(q_star).quantize(Q2, rounding=ROUND_HALF_UP)


def eoq_maturing(
    annual_demand: float | Decimal,
    order_setup_cost: float | Decimal,
    holding_cost_per_unit_day: float | Decimal,
    unit_purchase_cost: float | Decimal,
    price_at_age_fn: Callable[[int], float | Decimal],
    min_age_days: int = 1,
    max_age_days: int = 1825,
    step_days: int = 1,
) -> Dict[str, Any]:
    """
    Zanoni et al. EOQ-M solver: jointly optimizes maturation release age T_m (days)
    and batch size Q to maximize net profit per day.
    """
    daily_demand = float(annual_demand) / 365.0
    k = float(order_setup_cost)
    h_day = float(holding_cost_per_unit_day)
    c_unit = float(unit_purchase_cost)
    if daily_demand <= 0 or h_day <= 0 or k < 0:
        raise ValueError("annual_demand and holding_cost_per_unit_day must be > 0")

    best: Dict[str, Any] | None = None
    q_sell = math.sqrt((2.0 * daily_demand * k) / h_day) if k > 0 else daily_demand

    for t_m in range(min_age_days, max_age_days + 1, max(1, step_days)):
        p_t = float(price_at_age_fn(t_m))
        # Net profit over one batch Q matured for t_m days and sold over Q / daily_demand days:
        aging_hold_cost = h_day * t_m * q_sell
        selling_hold_cost = 0.5 * h_day * (q_sell ** 2) / daily_demand
        cycle_days = (q_sell / daily_demand)
        net_profit = (p_t - c_unit) * q_sell - k - aging_hold_cost - selling_hold_cost
        profit_per_day = net_profit / cycle_days
        if best is None or profit_per_day > best["_raw_rate"]:
            best = {
                "_raw_rate": profit_per_day,
                "optimal_maturation_days": t_m,
                "optimal_order_qty": _d(q_sell).quantize(Q2, rounding=ROUND_HALF_UP),
                "release_unit_price": _d(p_t).quantize(Q2, rounding=ROUND_HALF_UP),
                "expected_daily_profit": _d(profit_per_day).quantize(Q2, rounding=ROUND_HALF_UP),
            }

    assert best is not None
    best.pop("_raw_rate", None)
    return best


def abc_xyz_classify(
    items: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Classifies SKUs on ABC (80/15/5 annual consumption value) and XYZ (demand CV: <=0.5 X, <=1.0 Y, >1.0 Z).
    Each dict in items expects: {'sku': str, 'unit_cost': number, 'period_demands': [numbers...]}.
    """
    enriched = []
    grand_total = 0.0
    for it in items:
        demands = [float(x) for x in it["period_demands"]]
        total_d = sum(demands)
        mean_d = total_d / len(demands) if demands else 0.0
        if len(demands) > 1 and mean_d > 0:
            var_d = sum((x - mean_d) ** 2 for x in demands) / len(demands)
            cv = math.sqrt(var_d) / mean_d
        else:
            cv = 0.0 if mean_d > 0 else 999.0
        val = total_d * float(it["unit_cost"])
        grand_total += val
        xyz = "X" if cv <= 0.5 else ("Y" if cv <= 1.0 else "Z")
        enriched.append({
            "sku": it["sku"],
            "annual_value": _d(val).quantize(Q2, rounding=ROUND_HALF_UP),
            "_raw_val": val,
            "cv": _d(cv).quantize(Q4, rounding=ROUND_HALF_UP),
            "xyz": xyz,
        })

    enriched.sort(key=lambda r: r["_raw_val"], reverse=True)
    cum = 0.0
    out = []
    for row in enriched:
        prev_share = (cum / grand_total) if grand_total > 0 else 1.0
        cum += row["_raw_val"]
        abc = "A" if prev_share < 0.80 else ("B" if prev_share < 0.95 else "C")
        out.append({
            "sku": row["sku"],
            "annual_value": row["annual_value"],
            "cv": row["cv"],
            "abc": abc,
            "xyz": row["xyz"],
            "matrix_class": f"{abc}{row['xyz']}",
        })
    return out
