#!/usr/bin/env python3
"""
Standalone CLI: Multi-Strategy Order Fulfillment & JIT Dispatch Scheduler
Usage:
  python3 dispatch-order.py --demo
  python3 dispatch-order.py --sku GRAIN-RICE-BASMATI --qty 100
  python3 dispatch-order.py --sku DAIRY-MILK-1L --qty 25 --json
"""

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import fulfill_order, jit_dispatch_offset, load_catalog_file  # noqa: E402


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _json_safe(res: dict) -> dict:
    return {k: v for k, v in res.items() if k != "reservation"}


def print_fulfillment(res: dict) -> None:
    print(f"\n[SKU: {res['sku']} | Requested: {res['requested_quantity']} | Reservation: {res['reservation_id']}]")
    print(f"Strategy : {res['strategy']}")
    print(f"Fulfilled: {res['fulfilled_quantity']} (Complete: {res['is_fully_fulfilled']})")
    for alloc in res["allocations"]:
        print(
            f"  -> Picked {alloc['quantity_picked']} from {alloc['batch_number']} "
            f"[{alloc['status']}, age {alloc['age_days']}d, quality {alloc['quality_score']*100:.1f}%] "
            f"@ ${alloc['unit_price']:.2f}/unit"
        )
    print(f"Total Order Value: ${res['total_order_value']:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute multi-strategy temporal order fulfillment.")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=SKILL_DIR / "examples" / "sample-catalog.json",
        help="Path to catalog JSON file",
    )
    parser.add_argument("--sku", type=str, help="SKU to dispatch")
    parser.add_argument("--qty", type=float, help="Quantity requested")
    parser.add_argument("--demo", action="store_true", help="Run multi-archetype dispatch & JIT demo")
    parser.add_argument("--date", type=str, default=None, help="Override evaluation date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    args = parser.parse_args()

    ref_date, items, lots = load_catalog_file(args.catalog)
    eval_date = date.fromisoformat(args.date) if args.date else ref_date

    if args.demo or not args.sku:
        demo_orders = [
            ("DAIRY-MILK-1L", 25.0),
            ("CEMT-OPC-53", 120.0),
            ("GRAIN-RICE-BASMATI", 100.0),
            ("WINE-CABERNET-RES", 10.0),
            ("AGGR-SAND-RIVER", 45.0),
        ]
        outputs = []
        for sku, qty in demo_orders:
            sku_lots = [l for l in lots if l.item.sku == sku]
            outputs.append(fulfill_order(sku_lots, qty, eval_date))

        pour_time = datetime(eval_date.year, eval_date.month, eval_date.day, 6, 0)
        dispatch_time = jit_dispatch_offset(pour_time, mean_transit_minutes=45, unload_minutes=20, std_transit_minutes=12)

        if args.json:
            print(json.dumps({
                "evaluation_date": eval_date.isoformat(),
                "orders": [_json_safe(o) for o in outputs],
                "jit_concrete_example": {
                    "scheduled_pour": pour_time.isoformat(),
                    "target_plant_dispatch": dispatch_time.isoformat(),
                },
            }, cls=_DecimalEncoder, indent=2))
            return 0

        print("=" * 88)
        print(f" MULTI-STRATEGY DISPATCH ROUTER | Date: {eval_date.isoformat()}")
        print("=" * 88)
        for res in outputs:
            print_fulfillment(res)
        print("\n" + "-" * 88)
        print(" JIT READY-MIX CONCRETE SYNCHRONIZATION")
        print(f"  Scheduled Slab Pour : {pour_time.strftime('%Y-%m-%d %H:%M')}")
        print(f"  Target Plant Dispatch: {dispatch_time.strftime('%Y-%m-%d %H:%M')} (97.5% on-time arrival)")
        print("=" * 88)
        return 0

    if args.sku not in items:
        print(f"[ERROR] Unknown SKU {args.sku!r}. Available: {sorted(items.keys())}", file=sys.stderr)
        return 1
    if not args.qty or args.qty <= 0:
        print("[ERROR] --qty must be > 0 when --sku is specified.", file=sys.stderr)
        return 1

    sku_lots = [l for l in lots if l.item.sku == args.sku]
    result = fulfill_order(sku_lots, args.qty, eval_date)
    if args.json:
        print(json.dumps(_json_safe(result), cls=_DecimalEncoder, indent=2))
    else:
        print_fulfillment(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
