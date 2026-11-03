#!/usr/bin/env python3
"""
Standalone CLI: Evaluate Inventory Stock Health, Quality Scores & Dynamic Pricing
Usage:
  python3 evaluate-stock.py [--catalog path.json] [--date YYYY-MM-DD] [--json]
"""

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import evaluate_lot, load_catalog_file  # noqa: E402


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate temporal inventory lot statuses and dynamic pricing.")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=SKILL_DIR / "examples" / "sample-catalog.json",
        help="Path to catalog JSON file (default: examples/sample-catalog.json)",
    )
    parser.add_argument("--date", type=str, default=None, help="Override evaluation date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    args = parser.parse_args()

    ref_date, _, lots = load_catalog_file(args.catalog)
    eval_date = date.fromisoformat(args.date) if args.date else ref_date

    results = []
    for lot in lots:
        status, price, quality = evaluate_lot(lot, eval_date)
        results.append({
            "lot_id": lot.lot_id,
            "sku": lot.item.sku,
            "name": lot.item.name,
            "archetype": lot.item.archetype.value,
            "batch_number": lot.batch_number,
            "age_days": lot.age_days(eval_date),
            "days_to_expiry": lot.days_to_expiry(eval_date),
            "available_qty": lot.available_quantity,
            "status": status.value,
            "quality_pct": round(quality * 100.0, 1),
            "base_price": lot.item.base_selling_price,
            "dynamic_price": price,
        })

    if args.json:
        print(json.dumps({"evaluation_date": eval_date.isoformat(), "lots": results}, cls=_DecimalEncoder, indent=2))
        return 0

    print("=" * 94)
    print(f" TEMPORAL INVENTORY EVALUATION | Date: {eval_date.isoformat()} | Catalog: {args.catalog.name}")
    print("=" * 94)
    print(f"{'LOT':<5} | {'ITEM NAME':<32} | {'ARCHETYPE':<11} | {'AGE':<6} | {'STATUS':<17} | {'QUALITY':<7} | {'PRICE':<8}")
    print("-" * 94)
    for row in results:
        age_str = f"{row['age_days']}d"
        print(
            f"{row['lot_id']:<5} | {row['name'][:32]:<32} | {row['archetype']:<11} | "
            f"{age_str:<6} | {row['status']:<17} | {row['quality_pct']:>6.1f}% | ${row['dynamic_price']:>6.2f}"
        )
    print("=" * 94)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
