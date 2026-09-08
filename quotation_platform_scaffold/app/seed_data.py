"""
Default category set, traced from the source workbook's 'Tag Codes' sheet
(category -> two-letter tag prefix) joined to the 'Lists' sheet's
'Process Sequence - Category' table (category -> print order in the quote).
These are global defaults (account_id = NULL); an account can add its own
categories on top without touching this list.
"""
from sqlalchemy.orm import Session

from app.db_models import Category, FitOutBenchmark

# (code, name, two_letter_code, process_sequence)
DEFAULT_CATEGORIES = [
    ("01", "Design", "DS", 1),
    ("02", "Preliminaries", "PR", 2),
    ("03", "Dismantling", "DM", 3),
    ("04", "Demolishing", "DL", 4),
    ("16", "Sub structure work", "SS", 5),
    ("35", "Super structure", "SP", 6),
    ("12", "Waterproofing", "WP", 7),
    ("10", "HVAC", "HV", 8),
    ("09", "Plumbing", "PL", 9),
    ("08", "Electrical", "EL", 10),
    ("11", "Fire System", "FS", 11),
    ("15", "Gas", "GS", 12),
    ("05", "Walls", "WL", 13),
    ("07", "Ceiling", "CL", 14),
    ("19", "Substrates & Ancillary Materials", "SB", 15),
    ("06", "Flooring", "FL", 16),
    ("13", "Doors & Openings", "DR", 17),
    ("14", "Windows", "WD", 18),
    ("20", "Glass & Glazing", "GL", 19),
    ("21", "Metal Finishes", "MF", 20),
    ("22", "Joinery, Millwork & Metal Works", "JM", 21),
    ("23", "Kitchen Cabinets", "KC", 22),
    ("24", "Finishes", "FN", 23),
    ("17", "Exterior", "EX", 24),
    ("18", "Landscape & Hardscape Elements", "LH", 25),
    ("27", "Fixtures", "FX", 26),
    ("31", "Equipments", "EQ", 27),
    ("32", "Signage", "SG", 28),
    ("25", "Storage", "ST", 29),
    ("26", "Furniture", "FR", 30),
    ("28", "Outdoor Furnitures", "OF", 31),
    ("29", "Textiles & Soft Finishes", "TX", 32),
    ("30", "Accessories", "AC", 33),
    ("33", "Refurbishment", "RF", 34),
    ("34", "Project Close Out Works", "PC", 35),
]


def seed_default_categories(db: Session) -> None:
    already_seeded = db.query(Category).filter(Category.account_id.is_(None)).first()
    if already_seeded:
        return
    for code, name, two_letter_code, process_sequence in DEFAULT_CATEGORIES:
        db.add(
            Category(
                account_id=None,
                code=code,
                name=name,
                two_letter_code=two_letter_code,
                process_sequence=process_sequence,
            )
        )
    db.commit()


# (project_type, trade_element, low_bhd_per_sqm, high_bhd_per_sqm), traced from the
# source workbook's 'Fit-Out Benchmarks' sheet. Indicative sense-check only — the
# sheet itself notes these are "rates supplied by user... not from a published rate
# book", not wired into any pricing math here either.
DEFAULT_FITOUT_BENCHMARKS = [
    ("Office", "MEP (elec, HVAC, data, fire)", 9.00, 24.00),
    ("Office", "Partitions", 4.50, 12.00),
    ("Office", "Flooring", 4.50, 12.00),
    ("Office", "Ceiling", 3.00, 8.00),
    ("Office", "Joinery & finishes", 9.00, 24.00),
    ("Retail", "MEP (elec, HVAC, data, fire)", 12.00, 30.00),
    ("Retail", "Partitions", 6.00, 15.00),
    ("Retail", "Flooring", 6.00, 15.00),
    ("Retail", "Ceiling", 4.00, 10.00),
    ("Retail", "Joinery & finishes", 12.00, 30.00),
    ("Restaurant", "MEP (elec, HVAC, data, fire)", 18.00, 45.00),
    ("Restaurant", "Partitions", 9.00, 22.50),
    ("Restaurant", "Flooring", 9.00, 22.50),
    ("Restaurant", "Ceiling", 6.00, 15.00),
    ("Restaurant", "Joinery & finishes", 18.00, 45.00),
]


def seed_default_fitout_benchmarks(db: Session) -> None:
    already_seeded = db.query(FitOutBenchmark).filter(FitOutBenchmark.account_id.is_(None)).first()
    if already_seeded:
        return
    for project_type, trade_element, low, high in DEFAULT_FITOUT_BENCHMARKS:
        db.add(
            FitOutBenchmark(
                account_id=None,
                project_type=project_type,
                trade_element=trade_element,
                low_bhd_per_sqm=low,
                high_bhd_per_sqm=high,
            )
        )
    db.commit()
