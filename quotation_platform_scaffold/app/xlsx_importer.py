"""
Imports a real ADC-BOQ-style workbook instead of typing items in one at a
time: '3.Selected' is already a flat, one-row-per-selected-item list (no
header-row logic needed — that only exists in the *rendered* 'ADC'/
'Consilio' sheets, which insert category-header rows on top of this same
data). We read every field 3.Selected carries, plus the client/project/
quote-number header cells shown on the 'ADC' sheet, and reproduce exactly
what that sheet displays: a Project, a Quote, and its category-grouped line
items — upserting CatalogItems along the way so the account's catalog picks
up real data too.

Column layout traced from the source workbook (data starts at row 3 in
'3.Selected'; row 2 is headers, row 1 is a caption):
  B=Category ("01.Design")   D=Tag ("DS-02")             E=Item Description
  F=Quantity                 G=Unit                       L=Unit Cost (BHD) [client]
  U=Supplier Unit Cost (BHD)
'ADC' sheet header cells: B9=client, B10=project name, C10=quote number,
E9=project number (itself a formula to '1.Project paramiters'!I1).
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

import openpyxl
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db_models import (
    Account, CatalogItem, Category, DocTemplate, Project, Quote, QuoteLineItem,
)

_CATEGORY_PREFIX_RE = re.compile(r"^\s*\d+\.\s*")
MAX_ROWS = 2000  # sane upper bound so a malformed sheet can't loop forever


def _find_sheet(wb, *candidates: str):
    by_lower = {name.lower(): name for name in wb.sheetnames}
    for cand in candidates:
        if cand.lower() in by_lower:
            return wb[by_lower[cand.lower()]]
    for name in wb.sheetnames:
        for cand in candidates:
            if cand.lower().replace(" ", "") in name.lower().replace(" ", ""):
                return wb[name]
    return None


def _decimal_or_none(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _clean_category_name(raw) -> str:
    return _CATEGORY_PREFIX_RE.sub("", str(raw)).strip()


def import_adc_workbook(file_bytes: bytes, account_id: str, db: Session) -> dict:
    try:
        wb = openpyxl.load_workbook(BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise HTTPException(422, f"Could not read that file as an Excel workbook: {exc}")

    selected_ws = _find_sheet(wb, "3.Selected", "Selected")
    if selected_ws is None:
        raise HTTPException(422, "No '3.Selected' sheet found in this workbook.")

    adc_ws = _find_sheet(wb, "ADC")
    params_ws = _find_sheet(wb, "1.Project paramiters", "1.Project parameters")

    client_name = (
        (adc_ws["B9"].value if adc_ws else None)
        or (params_ws["B2"].value if params_ws else None)
        or "Imported Client"
    )
    project_name = (
        (adc_ws["B10"].value if adc_ws else None)
        or (params_ws["B1"].value if params_ws else None)
        or "Imported Project"
    )
    quote_number = adc_ws["C10"].value if adc_ws else None
    project_number = (
        (adc_ws["E9"].value if adc_ws else None)
        or (params_ws["I1"].value if params_ws else None)
    )
    project_number = str(project_number) if project_number is not None else None

    # --- categories: match by name (stripping the "NN." prefix), account-or-global ---
    account_categories = (
        db.query(Category)
        .filter((Category.account_id.is_(None)) | (Category.account_id == account_id))
        .all()
    )
    category_by_name = {c.name.strip().lower(): c for c in account_categories}
    next_sequence = max((c.process_sequence for c in account_categories), default=0) + 1

    def resolve_category(raw_name) -> Category:
        nonlocal next_sequence
        clean = _clean_category_name(raw_name)
        existing = category_by_name.get(clean.lower())
        if existing:
            return existing
        new_category = Category(
            account_id=account_id, code="99", name=clean,
            two_letter_code=(clean[:2].upper() or "XX"), process_sequence=next_sequence,
        )
        next_sequence += 1
        db.add(new_category)
        db.flush()
        category_by_name[clean.lower()] = new_category
        return new_category

    # --- '3.Selected' is already flat: one row per selected item, no header rows ---
    rows = []
    r = 3
    while r <= 2 + MAX_ROWS:
        category_raw = selected_ws.cell(row=r, column=2).value  # B
        description = selected_ws.cell(row=r, column=5).value   # E
        if not category_raw and not description:
            break
        rows.append({
            "category_raw": category_raw,
            "tag": selected_ws.cell(row=r, column=4).value,             # D
            "description": description,
            "quantity": selected_ws.cell(row=r, column=6).value,        # F
            "unit": selected_ws.cell(row=r, column=7).value,            # G
            "client_unit_price": selected_ws.cell(row=r, column=12).value,   # L
            "supplier_unit_cost": selected_ws.cell(row=r, column=21).value,  # U
        })
        r += 1

    if not rows:
        raise HTTPException(422, "'3.Selected' has no selected rows to import.")

    # --- project: reuse by (account, project_number) if we have one, else create ---
    project = None
    if project_number:
        project = (
            db.query(Project)
            .filter(Project.account_id == account_id, Project.project_number == project_number)
            .first()
        )
    if project is None:
        project = Project(
            account_id=account_id, project_number=project_number,
            project_name=str(project_name), client_name=str(client_name),
        )
        db.add(project)
        db.flush()

    # --- quote ---
    account = db.get(Account, account_id)
    doc_template = (
        db.query(DocTemplate)
        .filter(DocTemplate.account_id == account_id, DocTemplate.is_default.is_(True))
        .first()
    )
    if not quote_number:
        existing_count = db.query(Quote).filter(Quote.project_id == project.id).count()
        prefix = project.project_number or project.id[:8]
        quote_number = f"{prefix}-{datetime.utcnow().year}-{existing_count + 1}"

    quote = Quote(
        account_id=account_id, project_id=project.id, quote_number=str(quote_number),
        client_name=str(client_name), vat_rate_snapshot=account.vat_rate_default,
        payment_terms=doc_template.default_payment_terms if doc_template else [],
        doc_template_id=doc_template.id if doc_template else None,
    )
    db.add(quote)
    db.flush()

    # --- catalog items (upsert by tag within the account) + line items ---
    existing_by_tag = {
        item.tag: item
        for item in db.query(CatalogItem)
        .filter(CatalogItem.account_id == account_id, CatalogItem.tag.isnot(None))
        .all()
    }
    sort_order_by_category: dict[str, int] = {}
    catalog_created, catalog_updated = 0, 0

    for row_data in rows:
        category = resolve_category(row_data["category_raw"] or "Uncategorized")
        tag = str(row_data["tag"]) if row_data["tag"] else None
        description = str(row_data["description"] or "")
        unit = str(row_data["unit"] or "Item")
        quantity = _decimal_or_none(row_data["quantity"]) or Decimal("1")
        client_price = _decimal_or_none(row_data["client_unit_price"]) or Decimal("0")
        supplier_cost = _decimal_or_none(row_data["supplier_unit_cost"])

        catalog_item = existing_by_tag.get(tag) if tag else None
        if catalog_item is not None:
            catalog_item.category_id = category.id
            catalog_item.description = description
            catalog_item.default_unit = unit
            catalog_item.client_unit_price = client_price
            catalog_item.supplier_unit_cost = supplier_cost
            catalog_updated += 1
        else:
            catalog_item = CatalogItem(
                account_id=account_id, category_id=category.id, tag=tag, description=description,
                default_unit=unit, client_unit_price=client_price, supplier_unit_cost=supplier_cost,
            )
            db.add(catalog_item)
            db.flush()
            if tag:
                existing_by_tag[tag] = catalog_item
            catalog_created += 1

        sort_order = sort_order_by_category.get(category.id, 0)
        sort_order_by_category[category.id] = sort_order + 1

        db.add(QuoteLineItem(
            quote_id=quote.id, catalog_item_id=catalog_item.id, category_id=category.id,
            tag=tag, description=description, unit=unit, quantity=quantity,
            client_unit_price=client_price, supplier_unit_cost=supplier_cost, sort_order=sort_order,
        ))

    db.commit()
    db.refresh(quote)
    db.refresh(project)

    return {
        "project": project,
        "quote": quote,
        "line_items_imported": len(rows),
        "catalog_items_created": catalog_created,
        "catalog_items_updated": catalog_updated,
    }
