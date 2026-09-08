"""Shared defaults, referenced from both the ORM insert path (app/main.py's
account creation) and the Pydantic schema defaults (app/models.py) so the two
can't drift apart."""

DEFAULT_PAYMENT_TERMS = [
    {"pct": 70, "label": "Advance on signing the Quotation"},
    {"pct": 30, "label": "After submitting the Drawings"},
]
