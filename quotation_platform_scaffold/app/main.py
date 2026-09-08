"""
FastAPI app for the multi-tenant, catalog-driven quotation platform.

Flow: account (+ branding + terms) -> catalog items -> project (+ rooms) ->
quote (+ line items, added from the catalog or one-off) -> calculate (live
price preview) -> generate-document / generate-spreadsheet (branded .docx / .xlsx).

Every account-scoped and resource-scoped endpoint requires the Bearer session
token from /auth/redeem and checks the caller actually owns the resource
(see app/auth.py) — a path segment naming an account/project/quote id proves
nothing on its own.
"""
import os
import secrets
import tempfile
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app import models
from app.auth import create_access_token, get_current_account, get_current_role, require_account_match, require_owner
from app.billing import create_checkout_session, handle_webhook_event, require_active_subscription, verify_webhook
from app.constants import DEFAULT_PAYMENT_TERMS
from app.cost_buildup import calculate_cost_buildup
from app.database import Base, SessionLocal, engine, get_db
from app.db_models import (
    Account, CatalogItem, Category, CostBuildupDefaults, DocTemplate, FitOutBenchmark,
    Project, Quote, QuoteLineItem, RateLibraryEntry, Room, TermsTemplate, User,
)
from app.docx_generator import generate_quote_document
from app.pricing_engine import calculate_quote_totals, group_line_items_by_category
from app.security import hash_access_code, hash_password, verify_access_code, verify_password
from app.seed_data import seed_default_categories, seed_default_fitout_benchmarks
from app.storage import get_asset_storage
from app.xlsx_generator import generate_quote_spreadsheet
from app.xlsx_importer import import_adc_workbook

app = FastAPI(title="Designer Quotation Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = os.environ.get("QUOTE_OUTPUT_DIR", os.path.join(tempfile.gettempdir(), "generated_quotes"))
os.makedirs(OUTPUT_DIR, exist_ok=True)

if os.environ.get("STORAGE_BACKEND", "local").lower() != "s3":
    # Local backend serves uploaded logos from this mount; S3 doesn't need it
    # (S3Storage.url_for returns a public/CDN URL directly).
    ASSET_DIR = os.environ.get("ASSET_DIR", os.path.join(os.path.dirname(__file__), "..", "uploaded_assets"))
    os.makedirs(os.path.join(ASSET_DIR, "logos"), exist_ok=True)
    app.mount("/static", StaticFiles(directory=ASSET_DIR), name="static")


AUTO_CREATE_TABLES = os.environ.get("AUTO_CREATE_TABLES", "true").lower() != "false"


@app.on_event("startup")
def on_startup():
    if AUTO_CREATE_TABLES:
        # Zero-setup dev convenience. In production, set AUTO_CREATE_TABLES=false
        # and run `alembic upgrade head` instead — see migrations/.
        Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_default_categories(db)
        seed_default_fitout_benchmarks(db)
    finally:
        db.close()


# --- ownership lookups --------------------------------------------------------------
# Resources not addressed under /accounts/{account_id}/... still belong to an
# account through a FK chain; these load-and-check helpers 404 (rather than
# leak existence via 403) when the caller doesn't own the resource.

def get_owned_project(db: Session, project_id: str, current: Account) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.account_id != current.id:
        raise HTTPException(404, "Project not found")
    return project


def get_owned_quote(db: Session, quote_id: str, current: Account) -> Quote:
    quote = db.get(Quote, quote_id)
    if quote is None or quote.account_id != current.id:
        raise HTTPException(404, "Quote not found")
    return quote


# --- accounts / auth ----------------------------------------------------------------

@app.post("/accounts", response_model=models.Account)
def create_account(payload: models.AccountCreate, db: Session = Depends(get_db)):
    account = Account(
        firm_name=payload.firm_name,
        access_code_hash=hash_access_code(payload.access_code),
        vat_rate_default=payload.vat_rate_default,
    )
    db.add(account)
    db.flush()

    db.add(DocTemplate(account_id=account.id, default_payment_terms=list(DEFAULT_PAYMENT_TERMS)))
    db.commit()
    db.refresh(account)
    return account


@app.post("/auth/redeem", response_model=models.TokenResponse)
def redeem_access_code(payload: models.AccessCodeRedeem, db: Session = Depends(get_db)):
    for account in db.query(Account).all():
        if verify_access_code(payload.access_code, account.access_code_hash):
            return models.TokenResponse(access_token=create_access_token(account.id), account=account, role="owner")
    raise HTTPException(404, "Invalid access code")


@app.post("/auth/login", response_model=models.TokenResponse)
def login_with_password(payload: models.LoginRequest, db: Session = Depends(get_db)):
    """Per-user login, alongside (not replacing) the account-wide access
    code: a firm's owner redeems the access code once to bootstrap the
    account, then adds named users who each log in with their own email
    and password from here on."""
    candidates = db.query(User).filter(User.email == payload.email).all()
    for user in candidates:
        if verify_password(payload.password, user.password_hash):
            account = db.get(Account, user.account_id)
            return models.TokenResponse(
                access_token=create_access_token(account.id, role=user.role), account=account, role=user.role
            )
    raise HTTPException(404, "Invalid email or password")


@app.get("/auth/me", response_model=models.SessionInfo)
def get_current_session_account(
    current: Account = Depends(get_current_account), role: str = Depends(get_current_role)
):
    """Session restore: resolves the account (and role) purely from the Bearer token."""
    return models.SessionInfo(account=current, role=role)


@app.post("/accounts/{account_id}/users", response_model=models.User)
def create_user(
    account_id: str, payload: models.UserCreate,
    db: Session = Depends(get_db), current: Account = Depends(require_owner),
):
    require_account_match(account_id, current)
    user = User(
        account_id=account_id, name=payload.name, email=payload.email,
        role=payload.role, password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.get("/accounts/{account_id}/users", response_model=list[models.User])
def list_users(
    account_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    require_account_match(account_id, current)
    return db.query(User).filter(User.account_id == account_id).all()


# --- billing ----------------------------------------------------------------

@app.post("/accounts/{account_id}/billing/checkout-session", response_model=models.CheckoutSessionOut)
def create_billing_checkout_session(
    account_id: str, payload: models.CheckoutSessionCreate,
    current: Account = Depends(require_owner),
):
    require_account_match(account_id, current)
    url = create_checkout_session(current, payload.success_url, payload.cancel_url)
    return models.CheckoutSessionOut(checkout_url=url)


@app.post("/billing/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    event = verify_webhook(payload, request.headers.get("stripe-signature"))
    handle_webhook_event(event, db)
    return {"status": "ok"}


# --- branding / terms ----------------------------------------------------------------

@app.post("/accounts/{account_id}/doc-templates", response_model=models.DocTemplate)
def create_doc_template(
    account_id: str, payload: models.DocTemplateCreate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    template = DocTemplate(account_id=account_id, **payload.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@app.get("/accounts/{account_id}/doc-templates", response_model=list[models.DocTemplate])
def list_doc_templates(
    account_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    require_account_match(account_id, current)
    return db.query(DocTemplate).filter(DocTemplate.account_id == account_id).all()


ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/svg+xml"}


@app.post("/accounts/{account_id}/doc-templates/{template_id}/logo", response_model=models.DocTemplate)
def upload_logo(
    account_id: str, template_id: str, file: UploadFile,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    template = db.get(DocTemplate, template_id)
    if template is None or template.account_id != account_id:
        raise HTTPException(404, "Doc template not found")
    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(422, f"Unsupported image type: {file.content_type}")

    storage = get_asset_storage()
    template.logo_asset_path = storage.save(file.file.read(), file.filename or "logo.png", subdir="logos")
    db.commit()
    db.refresh(template)
    return template


@app.patch("/accounts/{account_id}/doc-templates/{template_id}", response_model=models.DocTemplate)
def update_doc_template(
    account_id: str, template_id: str, payload: models.DocTemplateUpdate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    template = db.get(DocTemplate, template_id)
    if template is None or template.account_id != account_id:
        raise HTTPException(404, "Doc template not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(template, field, value)
    db.commit()
    db.refresh(template)
    return template


@app.post("/accounts/{account_id}/terms-templates", response_model=models.TermsTemplateOut)
def create_terms_template(
    account_id: str, payload: models.TermsTemplateCreate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    template = TermsTemplate(account_id=account_id, **payload.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@app.get("/accounts/{account_id}/terms-templates", response_model=list[models.TermsTemplateOut])
def list_terms_templates(
    account_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    require_account_match(account_id, current)
    return db.query(TermsTemplate).filter(TermsTemplate.account_id == account_id).all()


# --- categories ----------------------------------------------------------------------

@app.get("/accounts/{account_id}/categories", response_model=list[models.Category])
def list_categories(
    account_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    require_account_match(account_id, current)
    return (
        db.query(Category)
        .filter((Category.account_id.is_(None)) | (Category.account_id == account_id))
        .order_by(Category.process_sequence)
        .all()
    )


@app.post("/accounts/{account_id}/categories", response_model=models.Category)
def create_category(
    account_id: str, payload: models.CategoryCreate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    category = Category(account_id=account_id, **payload.model_dump())
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


# --- catalog items ----------------------------------------------------------------------

@app.post("/accounts/{account_id}/catalog-items", response_model=models.CatalogItem)
def create_catalog_item(
    account_id: str, payload: models.CatalogItemCreate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    item = CatalogItem(account_id=account_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.get("/accounts/{account_id}/catalog-items", response_model=list[models.CatalogItem])
def list_catalog_items(
    account_id: str, category_id: str | None = None,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    query = db.query(CatalogItem).filter(CatalogItem.account_id == account_id)
    if category_id:
        query = query.filter(CatalogItem.category_id == category_id)
    return query.all()


@app.get("/accounts/{account_id}/catalog-items/{item_id}", response_model=models.CatalogItem)
def get_catalog_item(
    account_id: str, item_id: str,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    item = db.get(CatalogItem, item_id)
    if item is None or item.account_id != account_id:
        raise HTTPException(404, "Catalog item not found")
    return item


@app.patch("/accounts/{account_id}/catalog-items/{item_id}", response_model=models.CatalogItem)
def update_catalog_item(
    account_id: str, item_id: str, payload: models.CatalogItemUpdate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    item = db.get(CatalogItem, item_id)
    if item is None or item.account_id != account_id:
        raise HTTPException(404, "Catalog item not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@app.delete("/accounts/{account_id}/catalog-items/{item_id}", status_code=204)
def delete_catalog_item(
    account_id: str, item_id: str,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    item = db.get(CatalogItem, item_id)
    if item is None or item.account_id != account_id:
        raise HTTPException(404, "Catalog item not found")
    db.delete(item)
    db.commit()


# --- projects / rooms ----------------------------------------------------------------------

@app.post("/accounts/{account_id}/projects", response_model=models.Project)
def create_project(
    account_id: str, payload: models.ProjectCreate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    project = Project(account_id=account_id, **payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@app.get("/accounts/{account_id}/projects", response_model=list[models.Project])
def list_projects(
    account_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    require_account_match(account_id, current)
    return db.query(Project).filter(Project.account_id == account_id).all()


@app.post("/accounts/{account_id}/import/workbook", response_model=models.WorkbookImportResponse)
async def import_workbook(
    account_id: str, file: UploadFile,
    db: Session = Depends(get_db), current: Account = Depends(require_active_subscription),
):
    """Reads '3.Selected' (already a flat, one-row-per-item list) plus the
    client/project/quote-number header cells from the 'ADC' sheet, and
    creates a Project + Quote + CatalogItems reproducing exactly what that
    sheet shows — see app/xlsx_importer.py for the full column mapping."""
    require_account_match(account_id, current)
    content = await file.read()
    result = import_adc_workbook(content, account_id, db)
    return models.WorkbookImportResponse(**result)


@app.get("/projects/{project_id}", response_model=models.Project)
def get_project(
    project_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    return get_owned_project(db, project_id, current)


@app.post("/projects/{project_id}/rooms", response_model=models.Room)
def create_room(
    project_id: str, payload: models.RoomCreate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    get_owned_project(db, project_id, current)
    room = Room(project_id=project_id, **payload.model_dump())
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


@app.get("/projects/{project_id}/rooms", response_model=list[models.Room])
def list_rooms(
    project_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    get_owned_project(db, project_id, current)
    return db.query(Room).filter(Room.project_id == project_id).all()


# --- quotes ----------------------------------------------------------------------

def _resolve_line_item_fields(db: Session, account_id: str, payload: models.QuoteLineItemCreate) -> dict:
    if payload.catalog_item_id:
        catalog_item = db.get(CatalogItem, payload.catalog_item_id)
        if catalog_item is None or catalog_item.account_id != account_id:
            raise HTTPException(404, "Catalog item not found")
        return {
            "catalog_item_id": catalog_item.id,
            "category_id": catalog_item.category_id,
            "tag": payload.tag or catalog_item.tag,
            "description": payload.description or catalog_item.description,
            "unit": payload.unit or catalog_item.default_unit,
            "client_unit_price": (
                payload.client_unit_price
                if payload.client_unit_price is not None
                else (catalog_item.client_unit_price or Decimal("0"))
            ),
            "supplier_unit_cost": (
                payload.supplier_unit_cost
                if payload.supplier_unit_cost is not None
                else catalog_item.supplier_unit_cost
            ),
        }

    if not payload.category_id or not payload.description or payload.client_unit_price is None:
        raise HTTPException(
            422,
            "A custom line item (no catalog_item_id) requires category_id, description and client_unit_price.",
        )
    return {
        "catalog_item_id": None,
        "category_id": payload.category_id,
        "tag": payload.tag,
        "description": payload.description,
        "unit": payload.unit or "Item",
        "client_unit_price": payload.client_unit_price,
        "supplier_unit_cost": payload.supplier_unit_cost,
    }


@app.get("/projects/{project_id}/quotes", response_model=list[models.QuoteOut])
def list_quotes_for_project(
    project_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    get_owned_project(db, project_id, current)
    return db.query(Quote).filter(Quote.project_id == project_id).order_by(Quote.created_at.desc()).all()


@app.post("/accounts/{account_id}/quotes", response_model=models.QuoteOut)
def create_quote(
    account_id: str, payload: models.QuoteCreate,
    db: Session = Depends(get_db), current: Account = Depends(require_active_subscription),
):
    require_account_match(account_id, current)
    project = db.get(Project, payload.project_id)
    if project is None or project.account_id != account_id:
        raise HTTPException(404, "Project not found for this account")

    doc_template = (
        db.get(DocTemplate, payload.doc_template_id)
        if payload.doc_template_id
        else db.query(DocTemplate).filter(DocTemplate.account_id == account_id, DocTemplate.is_default.is_(True)).first()
    )

    payment_terms = (
        [t.model_dump() for t in payload.payment_terms]
        if payload.payment_terms
        else (doc_template.default_payment_terms if doc_template else [])
    )

    quote_number = payload.quote_number
    if not quote_number:
        existing_count = db.query(Quote).filter(Quote.project_id == project.id).count()
        prefix = project.project_number or project.id[:8]
        quote_number = f"{prefix}-{datetime.utcnow().year}-{existing_count + 1}"

    quote = Quote(
        account_id=account_id,
        project_id=project.id,
        quote_number=quote_number,
        client_name=payload.client_name,
        vat_rate_snapshot=payload.vat_rate if payload.vat_rate is not None else current.vat_rate_default,
        payment_terms=payment_terms,
        doc_template_id=doc_template.id if doc_template else None,
        share_token=secrets.token_urlsafe(24),
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


@app.get("/quotes/{quote_id}", response_model=models.QuoteOut)
def get_quote(
    quote_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    return get_owned_quote(db, quote_id, current)


def _reject_if_accepted(quote: Quote) -> None:
    if quote.status == "accepted":
        raise HTTPException(409, "This quote has been accepted by the client and can no longer be edited.")


@app.post("/quotes/{quote_id}/line-items", response_model=models.QuoteLineItemOut)
def add_line_item(
    quote_id: str, payload: models.QuoteLineItemCreate,
    db: Session = Depends(get_db), current: Account = Depends(require_active_subscription),
):
    quote = get_owned_quote(db, quote_id, current)
    _reject_if_accepted(quote)
    fields = _resolve_line_item_fields(db, quote.account_id, payload)
    max_sort = db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == quote_id).count()
    line_item = QuoteLineItem(
        quote_id=quote_id,
        quantity=payload.quantity,
        room_id=payload.room_id,
        sort_order=max_sort,
        **fields,
    )
    db.add(line_item)
    db.commit()
    db.refresh(line_item)
    return line_item


@app.patch("/quotes/{quote_id}/line-items/{line_item_id}", response_model=models.QuoteLineItemOut)
def update_line_item(
    quote_id: str, line_item_id: str, payload: models.QuoteLineItemUpdate,
    db: Session = Depends(get_db), current: Account = Depends(require_active_subscription),
):
    quote = get_owned_quote(db, quote_id, current)
    _reject_if_accepted(quote)
    line_item = db.get(QuoteLineItem, line_item_id)
    if line_item is None or line_item.quote_id != quote_id:
        raise HTTPException(404, "Line item not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(line_item, field, value)
    db.commit()
    db.refresh(line_item)
    return line_item


@app.delete("/quotes/{quote_id}/line-items/{line_item_id}", status_code=204)
def delete_line_item(
    quote_id: str, line_item_id: str,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    quote = get_owned_quote(db, quote_id, current)
    _reject_if_accepted(quote)
    line_item = db.get(QuoteLineItem, line_item_id)
    if line_item is None or line_item.quote_id != quote_id:
        raise HTTPException(404, "Line item not found")
    db.delete(line_item)
    db.commit()


@app.get("/quotes/{quote_id}/calculate", response_model=models.QuoteCalculateResponse)
def calculate_quote(
    quote_id: str, db: Session = Depends(get_db), current: Account = Depends(require_active_subscription)
):
    """Live pricing preview — call this on every form change in the UI."""
    quote = get_owned_quote(db, quote_id, current)
    groups = group_line_items_by_category(quote.line_items)
    totals = calculate_quote_totals(quote.line_items, quote.vat_rate_snapshot, quote.payment_terms)

    return models.QuoteCalculateResponse(
        quote_id=quote.id,
        groups=[
            models.CategoryGroupOut(
                category_id=g["category"].id,
                category_name=g["category"].name,
                process_sequence=g["category"].process_sequence,
                items=g["items"],
                category_subtotal=g["subtotal"],
            )
            for g in groups
        ],
        subtotal=totals["subtotal"],
        vat_rate=totals["vat_rate"],
        vat_amount=totals["vat_amount"],
        total=totals["total"],
        payment_schedule=totals["payment_schedule"],
    )


def _load_generation_context(db: Session, quote: Quote, current: Account) -> tuple[Project, DocTemplate, TermsTemplate | None]:
    project = db.get(Project, quote.project_id)
    doc_template = db.get(DocTemplate, quote.doc_template_id) if quote.doc_template_id else None
    if doc_template is None:
        raise HTTPException(422, "Quote has no doc template assigned; create one for this account first.")
    terms_template = (
        db.query(TermsTemplate)
        .filter(TermsTemplate.account_id == current.id, TermsTemplate.is_default.is_(True))
        .first()
    )
    return project, doc_template, terms_template


@app.post("/quotes/{quote_id}/generate-document")
def generate_document(
    quote_id: str, db: Session = Depends(get_db), current: Account = Depends(require_active_subscription)
):
    quote = get_owned_quote(db, quote_id, current)
    project, doc_template, terms_template = _load_generation_context(db, quote, current)

    filename = f"quote_{uuid.uuid4().hex[:8]}.docx"
    output_path = os.path.join(OUTPUT_DIR, filename)
    generate_quote_document(
        account=current,
        doc_template=doc_template,
        project=project,
        quote=quote,
        terms_template=terms_template,
        output_path=output_path,
    )

    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@app.post("/quotes/{quote_id}/generate-spreadsheet")
def generate_spreadsheet(
    quote_id: str, db: Session = Depends(get_db), current: Account = Depends(require_active_subscription)
):
    quote = get_owned_quote(db, quote_id, current)
    project, doc_template, terms_template = _load_generation_context(db, quote, current)

    filename = f"quote_{uuid.uuid4().hex[:8]}.xlsx"
    output_path = os.path.join(OUTPUT_DIR, filename)
    generate_quote_spreadsheet(
        account=current,
        doc_template=doc_template,
        project=project,
        quote=quote,
        terms_template=terms_template,
        output_path=output_path,
    )

    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


# --- v2 estimation aids: Rate Library / cost build-up / Fit-Out Benchmarks --------
# Reference/estimation tools only — nothing here is wired into quote pricing math.

@app.get("/accounts/{account_id}/rate-library", response_model=list[models.RateLibraryEntry])
def list_rate_library_entries(
    account_id: str, search: str | None = None,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    query = db.query(RateLibraryEntry).filter(
        (RateLibraryEntry.account_id.is_(None)) | (RateLibraryEntry.account_id == account_id)
    )
    if search:
        query = query.filter(RateLibraryEntry.item_description.ilike(f"%{search}%"))
    return query.all()


@app.post("/accounts/{account_id}/rate-library", response_model=models.RateLibraryEntry)
def create_rate_library_entry(
    account_id: str, payload: models.RateLibraryEntryCreate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    entry = RateLibraryEntry(account_id=account_id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/accounts/{account_id}/rate-library/{entry_id}", status_code=204)
def delete_rate_library_entry(
    account_id: str, entry_id: str,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    entry = db.get(RateLibraryEntry, entry_id)
    if entry is None or entry.account_id != account_id:
        raise HTTPException(404, "Rate library entry not found")
    db.delete(entry)
    db.commit()


def _get_or_create_buildup_defaults(db: Session, account_id: str) -> CostBuildupDefaults:
    defaults = db.query(CostBuildupDefaults).filter(CostBuildupDefaults.account_id == account_id).first()
    if defaults is None:
        defaults = CostBuildupDefaults(account_id=account_id)
        db.add(defaults)
        db.commit()
        db.refresh(defaults)
    return defaults


@app.get("/accounts/{account_id}/cost-buildup/defaults", response_model=models.CostBuildupDefaultsOut)
def get_cost_buildup_defaults(
    account_id: str, db: Session = Depends(get_db), current: Account = Depends(get_current_account)
):
    require_account_match(account_id, current)
    return _get_or_create_buildup_defaults(db, account_id)


@app.patch("/accounts/{account_id}/cost-buildup/defaults", response_model=models.CostBuildupDefaultsOut)
def update_cost_buildup_defaults(
    account_id: str, payload: models.CostBuildupDefaultsUpdate,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    defaults = _get_or_create_buildup_defaults(db, account_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(defaults, field, value)
    db.commit()
    db.refresh(defaults)
    return defaults


@app.post("/accounts/{account_id}/cost-buildup/calculate", response_model=models.CostBuildupCalculateResponse)
def calculate_cost_buildup_endpoint(
    account_id: str, payload: models.CostBuildupCalculateRequest,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    defaults = _get_or_create_buildup_defaults(db, account_id)
    result = calculate_cost_buildup(
        material_cost_per_unit=payload.material_cost_per_unit,
        labour_cost_per_unit=payload.labour_cost_per_unit,
        quantity=payload.quantity,
        wastage_pct=payload.wastage_pct if payload.wastage_pct is not None else defaults.wastage_pct,
        overhead_pct=payload.overhead_pct if payload.overhead_pct is not None else defaults.overhead_pct,
        margin_pct=payload.margin_pct if payload.margin_pct is not None else defaults.margin_pct,
    )
    return models.CostBuildupCalculateResponse(**result)


@app.get("/accounts/{account_id}/fitout-benchmarks", response_model=list[models.FitOutBenchmark])
def list_fitout_benchmarks(
    account_id: str, project_type: str | None = None,
    db: Session = Depends(get_db), current: Account = Depends(get_current_account),
):
    require_account_match(account_id, current)
    query = db.query(FitOutBenchmark).filter(
        (FitOutBenchmark.account_id.is_(None)) | (FitOutBenchmark.account_id == account_id)
    )
    if project_type:
        query = query.filter(FitOutBenchmark.project_type == project_type)
    return query.all()


# --- public (client-facing) quote view -------------------------------------------
# No auth dependency at all: the share_token itself (an unguessable
# secrets.token_urlsafe(24), not the account's JWT) is the only credential —
# the client has no account to log into. Never exposes supplier_unit_cost.

def _build_public_quote_view(quote: Quote, db: Session) -> models.PublicQuoteView:
    account = db.get(Account, quote.account_id)
    project = db.get(Project, quote.project_id)
    doc_template = db.get(DocTemplate, quote.doc_template_id) if quote.doc_template_id else None
    terms_template = (
        db.query(TermsTemplate)
        .filter(TermsTemplate.account_id == account.id, TermsTemplate.is_default.is_(True))
        .first()
    )

    groups = group_line_items_by_category(quote.line_items)
    totals = calculate_quote_totals(quote.line_items, quote.vat_rate_snapshot, quote.payment_terms)

    return models.PublicQuoteView(
        firm_name=account.firm_name,
        logo_url=doc_template.logo_url if doc_template else None,
        primary_color=doc_template.primary_color if doc_template else "#2B2B2B",
        accent_color=doc_template.accent_color if doc_template else "#C9A24B",
        project_name=project.project_name,
        client_name=quote.client_name,
        quote_number=quote.quote_number,
        status=quote.status,
        groups=[
            models.PublicCategoryGroup(
                category_name=g["category"].name,
                items=[
                    models.PublicQuoteLineItem(
                        description=item.description, unit=item.unit,
                        quantity=item.quantity, line_total=item.line_total,
                    )
                    for item in g["items"]
                ],
                category_subtotal=g["subtotal"],
            )
            for g in groups
        ],
        subtotal=totals["subtotal"],
        vat_rate=totals["vat_rate"],
        vat_amount=totals["vat_amount"],
        total=totals["total"],
        payment_schedule=[models.PaymentScheduleRow(**row) for row in totals["payment_schedule"]],
        terms_sections=terms_template.sections if terms_template else [],
        accepted_at=quote.accepted_at,
        accepted_by_name=quote.accepted_by_name,
    )


def _get_quote_by_share_token(share_token: str, db: Session) -> Quote:
    quote = db.query(Quote).filter(Quote.share_token == share_token).first()
    if quote is None:
        raise HTTPException(404, "Quote not found")
    return quote


@app.get("/public/quotes/{share_token}", response_model=models.PublicQuoteView)
def get_public_quote(share_token: str, db: Session = Depends(get_db)):
    quote = _get_quote_by_share_token(share_token, db)
    return _build_public_quote_view(quote, db)


@app.post("/public/quotes/{share_token}/accept", response_model=models.PublicQuoteView)
def accept_public_quote(share_token: str, payload: models.QuoteAcceptRequest, db: Session = Depends(get_db)):
    quote = _get_quote_by_share_token(share_token, db)
    if quote.status == "accepted":
        raise HTTPException(409, "This quote has already been accepted.")
    if not payload.accepted_by_name.strip():
        raise HTTPException(422, "A name is required to accept this quote.")
    quote.status = "accepted"
    quote.accepted_at = datetime.utcnow()
    quote.accepted_by_name = payload.accepted_by_name.strip()
    db.commit()
    db.refresh(quote)
    return _build_public_quote_view(quote, db)


@app.get("/health")
def health():
    return {"status": "ok"}
