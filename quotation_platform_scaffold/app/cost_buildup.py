"""
Cost build-up calculator — traced from the source workbook's 'Rate Build-Up'
sheet: for a catalog item with no price yet, estimate a client_unit_price
from material/labour cost plus wastage/overhead/margin assumptions.

    wastage        = material_cost_per_unit x wastage_pct
    prime_cost     = material_cost_per_unit + labour_cost_per_unit + wastage
    overhead       = prime_cost x overhead_pct
    total_cost     = prime_cost + overhead
    margin         = total_cost x margin_pct
    proposed_unit_rate = total_cost + margin
    proposed_total     = proposed_unit_rate x quantity

This never writes to a catalog_item on its own — it's a calculator a
designer consults, then decides whether to accept the suggested price.
"""
from decimal import Decimal, ROUND_HALF_UP

THREE_DP = Decimal("0.001")


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(THREE_DP, rounding=ROUND_HALF_UP)


def calculate_cost_buildup(
    material_cost_per_unit: Decimal,
    labour_cost_per_unit: Decimal,
    quantity: Decimal,
    wastage_pct: Decimal,
    overhead_pct: Decimal,
    margin_pct: Decimal,
) -> dict:
    wastage = _q(material_cost_per_unit * wastage_pct / Decimal(100))
    prime_cost = _q(material_cost_per_unit + labour_cost_per_unit + wastage)
    overhead = _q(prime_cost * overhead_pct / Decimal(100))
    total_cost = _q(prime_cost + overhead)
    margin = _q(total_cost * margin_pct / Decimal(100))
    proposed_unit_rate = _q(total_cost + margin)
    proposed_total = _q(proposed_unit_rate * quantity)

    return {
        "wastage": wastage,
        "prime_cost": prime_cost,
        "overhead": overhead,
        "total_cost": total_cost,
        "margin": margin,
        "proposed_unit_rate": proposed_unit_rate,
        "proposed_total": proposed_total,
    }
