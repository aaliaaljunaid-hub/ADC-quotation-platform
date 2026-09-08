"""
Pricing engine — catalog-driven: line total is just quantity x unit price,
no tiers or fee methods. Grouping/ordering mirrors the ADC/Consilio sheets'
category-header logic (there implemented with Z/AA helper-column formulas
walking a sorted range; here it's a GROUP BY ordered by
categories.process_sequence).
"""
from decimal import Decimal, ROUND_HALF_UP

from app.db_models import Category, QuoteLineItem

THREE_DP = Decimal("0.001")


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(THREE_DP, rounding=ROUND_HALF_UP)


def group_line_items_by_category(line_items: list[QuoteLineItem]) -> list[dict]:
    groups: dict[str, dict] = {}
    for li in line_items:
        cat: Category = li.category
        bucket = groups.setdefault(
            cat.id, {"category": cat, "items": []}
        )
        bucket["items"].append(li)

    ordered = sorted(groups.values(), key=lambda g: g["category"].process_sequence)
    for group in ordered:
        group["items"].sort(key=lambda li: li.sort_order)
        group["subtotal"] = _q(sum((li.line_total for li in group["items"]), Decimal("0")))
    return ordered


def calculate_quote_totals(
    line_items: list[QuoteLineItem],
    vat_rate: Decimal,
    payment_terms: list[dict],
) -> dict:
    subtotal = _q(sum((li.line_total for li in line_items), Decimal("0")))
    vat_amount = _q(subtotal * Decimal(vat_rate) / Decimal(100))
    total = _q(subtotal + vat_amount)

    schedule = []
    for term in payment_terms:
        pct = Decimal(str(term["pct"]))
        amount = _q(total * pct / Decimal(100))
        schedule.append({"pct": float(pct), "label": term.get("label", ""), "amount": amount})

    return {
        "subtotal": subtotal,
        "vat_rate": Decimal(vat_rate),
        "vat_amount": vat_amount,
        "total": total,
        "payment_schedule": schedule,
    }
