"""
Retail, Supermarket, Apparel Variant Matrix & Serialized Electronics Engine:
1. Supermarket EAN-13 Variable-Weight / Price-Embedded Scale Barcode Parser & Checksum
2. Statutory Batch MRP Ceiling Guard & Promo Bundle / BOGO Calculators
3. Apparel Style-Color-Size Variant Matrix Generator & Size-Brokenness Clearance Detector
4. Customer Return Grading Router (Restock vs Open-Box vs Defective Quarantine)
5. Electronics Serial / IMEI Luhn Validator, Per-Unit Lifecycle & Dual Warranty Tracker
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import itertools
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


P2 = Decimal("0.01")
Q6 = Decimal("0.000001")


def _dec(val: float | int | str | Decimal) -> Decimal:
    return val if isinstance(val, Decimal) else Decimal(str(val))


# ---------------------------------------------------------------------------
# 1. Supermarket Variable-Weight / Price EAN-13 Scale Barcodes & Batch MRP
# ---------------------------------------------------------------------------

def ean13_check_digit(first_twelve_digits: str) -> str:
    if len(first_twelve_digits) != 12 or not first_twelve_digits.isdigit():
        raise ValueError(f"Expected 12 digits for EAN-13 check calculation, got {first_twelve_digits!r}")
    total = sum((int(ch) * (3 if idx % 2 == 1 else 1)) for idx, ch in enumerate(first_twelve_digits))
    return str((10 - (total % 10)) % 10)


def build_scale_ean13(prefix: str, plu_code: str, encoded_value: Decimal, decimals: int = 3) -> str:
    """
    Generates a standard 13-digit retail scale barcode: PP IIIII VVVVV C
    - prefix '20': weight-embedded (default 3 decimals -> kg, max 99.999 kg)
    - prefix '21': price-embedded (default 2 decimals -> currency, max 999.99)
    """
    if len(prefix) != 2 or not prefix.isdigit() or prefix[0] != "2":
        raise ValueError("Scale EAN-13 prefix must be 2 digits starting with '2' (e.g., '20' or '21')")
    plu = plu_code.zfill(5)
    if len(plu) != 5 or not plu.isdigit():
        raise ValueError("PLU code must be up to 5 digits")
    scaled = int((_dec(encoded_value) * (Decimal(10) ** decimals)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if scaled < 0 or scaled > 99999:
        raise ValueError(f"Encoded value {encoded_value} overflows 5-digit scale field")
    body = f"{prefix}{plu}{scaled:05d}"
    return body + ean13_check_digit(body)


def parse_scale_ean13(
    barcode: str,
    weight_prefixes: Sequence[str] = ("20", "22"),
    price_prefixes: Sequence[str] = ("21", "23"),
    weight_decimals: int = 3,
    price_decimals: int = 2,
) -> Dict[str, Any]:
    """Validates EAN-13 Modulo-10 check digit and extracts PLU + exact weight or price."""
    code = barcode.strip()
    if len(code) != 13 or not code.isdigit():
        raise ValueError(f"Invalid EAN-13 barcode length/format: {barcode!r}")
    expected_cd = ean13_check_digit(code[:12])
    if code[12] != expected_cd:
        raise ValueError(f"EAN-13 check digit mismatch on {barcode!r}: expected {expected_cd}, got {code[12]}")

    prefix = code[:2]
    plu = code[2:7]
    raw_val = int(code[7:12])
    if prefix in weight_prefixes:
        weight = (Decimal(raw_val) / (Decimal(10) ** weight_decimals)).quantize(Q6)
        return {"type": "WEIGHT_EMBEDDED", "prefix": prefix, "plu": plu, "weight_kg": weight, "price": None}
    if prefix in price_prefixes:
        price = (Decimal(raw_val) / (Decimal(10) ** price_decimals)).quantize(P2)
        return {"type": "PRICE_EMBEDDED", "prefix": prefix, "plu": plu, "weight_kg": None, "price": price}
    return {"type": "STANDARD_EAN13", "prefix": prefix, "ean13": code, "weight_kg": None, "price": None}


def enforce_mrp_ceiling(calculated_price: float | str | Decimal, batch_printed_mrp: Optional[float | str | Decimal]) -> Decimal:
    """Enforces statutory Maximum Retail Price (MRP) ceiling per batch."""
    p = _dec(calculated_price).quantize(P2, rounding=ROUND_HALF_UP)
    if batch_printed_mrp is None:
        return p
    mrp = _dec(batch_printed_mrp).quantize(P2, rounding=ROUND_HALF_UP)
    if mrp <= 0:
        raise ValueError("batch_printed_mrp must be > 0 when provided")
    return min(p, mrp)


def compute_bundle_availability(
    component_on_hand: Dict[str, float | str | Decimal],
    bundle_recipe: Dict[str, float | str | Decimal],
) -> int:
    """Computes maximum sellable promo/combo bundles limited by the scarcest component."""
    if not bundle_recipe:
        raise ValueError("bundle_recipe cannot be empty")
    limits: List[int] = []
    for sku, req in bundle_recipe.items():
        r = _dec(req)
        if r <= 0:
            raise ValueError(f"Bundle component {sku} requirement must be > 0")
        avail = _dec(component_on_hand.get(sku, 0))
        limits.append(max(0, int(avail // r)))
    return min(limits)


def compute_bogo_quantities(requested_paid_units: int, buy_x: int, get_y_free: int) -> Dict[str, int]:
    if requested_paid_units < 0 or buy_x <= 0 or get_y_free < 0:
        raise ValueError("Invalid BOGO parameters")
    free_units = (requested_paid_units // buy_x) * get_y_free
    return {
        "paid_units": requested_paid_units,
        "free_promo_units": free_units,
        "total_inventory_deducted": requested_paid_units + free_units,
    }


# ---------------------------------------------------------------------------
# 2. Apparel / Footwear Variant Matrix & Size-Curve Health
# ---------------------------------------------------------------------------

def generate_variant_matrix(
    parent_sku: str,
    base_name: str,
    base_price: float | str | Decimal,
    attribute_axes: Dict[str, Sequence[str]],
    price_surcharges: Optional[Dict[Tuple[str, str], float | str | Decimal]] = None,
) -> List[Dict[str, Any]]:
    """
    Expands a Parent Style across arbitrary axes (e.g. Color x Size x Fit) into child SKUs.
    price_surcharges allows e.g. ('size', 'XXL'): Decimal('2.00').
    """
    if not attribute_axes:
        raise ValueError("attribute_axes must define at least one dimension (e.g., color, size)")
    surcharges = price_surcharges or {}
    axis_names = list(attribute_axes.keys())
    axis_values = [list(attribute_axes[k]) for k in axis_names]
    if any(len(v) == 0 for v in axis_values):
        raise ValueError("Every attribute axis must contain at least one option")

    bp = _dec(base_price).quantize(P2, rounding=ROUND_HALF_UP)
    variants: List[Dict[str, Any]] = []
    for combo in itertools.product(*axis_values):
        attrs = dict(zip(axis_names, combo))
        suffix = "-".join(str(v).upper().replace(" ", "") for v in combo)
        extra = sum((_dec(surcharges.get((k, v), 0)) for k, v in attrs.items()), Decimal("0"))
        label = ", ".join(f"{k}={v}" for k, v in attrs.items())
        variants.append({
            "parent_sku": parent_sku,
            "variant_sku": f"{parent_sku}-{suffix}",
            "name": f"{base_name} ({label})",
            "attributes": attrs,
            "selling_price": (bp + extra).quantize(P2, rounding=ROUND_HALF_UP),
        })
    return variants


def evaluate_size_curve_health(
    size_on_hand: Dict[str, int | float | Decimal],
    target_weights: Dict[str, float],
    core_sizes: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """
    Detects 'size-broken' apparel assortments when high-velocity core sizes (e.g. M, L)
    stock out while fringe sizes accumulate dead stock.
    """
    if not target_weights:
        raise ValueError("target_weights cannot be empty")
    total_weight = sum(target_weights.values())
    if total_weight <= 0:
        raise ValueError("target_weights sum must be > 0")

    core = core_sizes or {
        sz for sz, w in target_weights.items() if (w / total_weight) >= 0.20
    }
    in_stock_sizes = {sz for sz, qty in size_on_hand.items() if _dec(qty) > 0}
    available_weight = sum(target_weights[sz] for sz in in_stock_sizes if sz in target_weights) / total_weight
    missing_core = sorted(core - in_stock_sizes)
    total_units = sum((_dec(q) for q in size_on_hand.values()), Decimal("0"))

    is_broken = total_units > 0 and (len(missing_core) > 0 or available_weight < 0.60)
    rec_discount = Decimal("0.00")
    if is_broken:
        rec_discount = Decimal("0.30") if available_weight < 0.40 else Decimal("0.15")

    return {
        "total_units_on_hand": total_units,
        "assortment_completeness_pct": round(available_weight * 100.0, 1),
        "missing_core_sizes": missing_core,
        "is_size_broken": is_broken,
        "recommended_clearance_discount": rec_discount,
    }


def grade_customer_return(
    condition_grade: str, original_unit_price: float | str | Decimal
) -> Dict[str, Any]:
    """Routes returned retail/apparel items into sellable, open-box markdown, or defective quarantine."""
    grade = condition_grade.strip().upper()
    price = _dec(original_unit_price).quantize(P2, rounding=ROUND_HALF_UP)
    if grade == "GRADE_A_NEW":
        return {"target_bucket": "SELLABLE_STOCK", "lifecycle_state": "NORMAL_AVAILABLE", "resale_price": price}
    if grade == "GRADE_B_OPEN_BOX":
        return {
            "target_bucket": "OPEN_BOX_CLEARANCE",
            "lifecycle_state": "WARNING_DISCOUNT",
            "resale_price": (price * Decimal("0.80")).quantize(P2, rounding=ROUND_HALF_UP),
        }
    if grade == "GRADE_C_DEFECTIVE":
        return {"target_bucket": "DEFECTIVE_QUARANTINE", "lifecycle_state": "QUARANTINED", "resale_price": Decimal("0.00")}
    raise ValueError(f"Unknown return condition_grade: {condition_grade!r}")


# ---------------------------------------------------------------------------
# 3. Electronics Per-Unit Serial / IMEI Lifecycle & Dual Warranty Engine
# ---------------------------------------------------------------------------

VALID_SERIAL_STATES = frozenset({
    "IN_STOCK",
    "RESERVED",
    "SOLD",
    "RMA_CUSTOMER_RETURN",
    "AT_VENDOR_SERVICE",
    "REPLACED_CLOSED",
    "SCRAPPED",
})


def validate_imei_luhn(imei: str) -> bool:
    """Validates a 15-digit mobile IMEI number using the GSMA Luhn check algorithm."""
    clean = imei.strip()
    if len(clean) != 15 or not clean.isdigit():
        return False
    total = 0
    for idx, ch in enumerate(clean):
        d = int(ch)
        if idx % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (total % 10) == 0


@dataclass
class SerializedUnit:
    serial_number: str
    sku: str
    lot_id: str
    grn_date: date
    vendor_warranty_months: int
    customer_warranty_months: int
    is_imei: bool = False
    state: str = "IN_STOCK"
    sold_date: Optional[date] = None
    customer_invoice_id: Optional[str] = None
    rma_ticket_id: Optional[str] = None
    replacement_serial: Optional[str] = None
    history: List[Tuple[str, str, date]] = field(default_factory=list)


class SerializedUnitRegistry:
    """Manages 1-unit = 1-unique-Serial/IMEI electronics, warranty windows, and RMA repairs."""

    def __init__(self) -> None:
        self._units: Dict[str, SerializedUnit] = {}

    def receive_unit(
        self,
        serial_number: str,
        sku: str,
        lot_id: str,
        grn_date: date,
        vendor_warranty_months: int = 12,
        customer_warranty_months: int = 12,
        is_imei: bool = False,
    ) -> SerializedUnit:
        sn = serial_number.strip()
        if not sn:
            raise ValueError("serial_number cannot be empty")
        if sn in self._units:
            raise ValueError(f"Duplicate serial/IMEI {sn!r} already registered")
        if is_imei and not validate_imei_luhn(sn):
            raise ValueError(f"Invalid 15-digit IMEI Luhn checksum: {sn!r}")

        unit = SerializedUnit(
            serial_number=sn,
            sku=sku,
            lot_id=lot_id,
            grn_date=grn_date,
            vendor_warranty_months=vendor_warranty_months,
            customer_warranty_months=customer_warranty_months,
            is_imei=is_imei,
            history=[("CREATED", "IN_STOCK", grn_date)],
        )
        self._units[sn] = unit
        return unit

    def sell_unit(self, serial_number: str, sold_date: date, customer_invoice_id: str) -> SerializedUnit:
        u = self._get(serial_number)
        if u.state not in ("IN_STOCK", "RESERVED"):
            raise ValueError(f"Cannot sell serial {serial_number!r} in state {u.state!r}")
        self._transition(u, "SOLD", sold_date)
        u.sold_date = sold_date
        u.customer_invoice_id = customer_invoice_id
        return u

    def open_rma(self, serial_number: str, rma_date: date, rma_ticket_id: str) -> Dict[str, Any]:
        u = self._get(serial_number)
        if u.state != "SOLD":
            raise ValueError(f"RMA requires unit in 'SOLD' state, got {u.state!r}")
        w = self.warranty_status(serial_number, rma_date)
        self._transition(u, "RMA_CUSTOMER_RETURN", rma_date)
        u.rma_ticket_id = rma_ticket_id
        return {"unit": u, **w}

    def dispatch_to_vendor_service(self, serial_number: str, event_date: date) -> SerializedUnit:
        u = self._get(serial_number)
        if u.state != "RMA_CUSTOMER_RETURN":
            raise ValueError(f"Unit {serial_number!r} must be in 'RMA_CUSTOMER_RETURN' before vendor dispatch")
        self._transition(u, "AT_VENDOR_SERVICE", event_date)
        return u

    def resolve_rma_with_swap(self, defective_serial: str, replacement_serial: str, swap_date: date) -> Tuple[SerializedUnit, SerializedUnit]:
        old_u = self._get(defective_serial)
        new_u = self._get(replacement_serial)
        if old_u.state not in ("RMA_CUSTOMER_RETURN", "AT_VENDOR_SERVICE"):
            raise ValueError(f"Defective unit {defective_serial!r} not in active RMA state")
        if new_u.state != "IN_STOCK" or new_u.sku != old_u.sku:
            raise ValueError(f"Replacement unit {replacement_serial!r} must be IN_STOCK and matching SKU")

        self._transition(old_u, "REPLACED_CLOSED", swap_date)
        old_u.replacement_serial = replacement_serial
        self._transition(new_u, "SOLD", swap_date)
        new_u.sold_date = old_u.sold_date or swap_date
        new_u.customer_invoice_id = old_u.customer_invoice_id
        return old_u, new_u

    def warranty_status(self, serial_number: str, check_date: date) -> Dict[str, Any]:
        u = self._get(serial_number)
        vendor_exp = u.grn_date + timedelta(days=int(round(u.vendor_warranty_months * 30.4375)))
        cust_exp = (
            u.sold_date + timedelta(days=int(round(u.customer_warranty_months * 30.4375)))
            if u.sold_date is not None
            else None
        )
        return {
            "serial_number": u.serial_number,
            "vendor_warranty_expiry": vendor_exp,
            "vendor_warranty_active": check_date <= vendor_exp,
            "customer_warranty_expiry": cust_exp,
            "customer_warranty_active": (cust_exp is not None and check_date <= cust_exp),
        }

    def _get(self, serial_number: str) -> SerializedUnit:
        if serial_number not in self._units:
            raise KeyError(f"Unknown serial/IMEI: {serial_number!r}")
        return self._units[serial_number]

    @staticmethod
    def _transition(u: SerializedUnit, new_state: str, event_date: date) -> None:
        if new_state not in VALID_SERIAL_STATES:
            raise ValueError(f"Invalid state: {new_state}")
        prev = u.state
        u.state = new_state
        u.history.append((prev, new_state, event_date))
