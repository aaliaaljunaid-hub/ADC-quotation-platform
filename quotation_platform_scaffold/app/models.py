"""
Pydantic request/response schemas for the catalog-driven quotation platform.
These mirror app/db_models.py (SQLAlchemy). All money fields are BHD,
3 decimal places.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field

from app.constants import DEFAULT_PAYMENT_TERMS


# --- Accounts / users / branding -------------------------------------------------

class AccountCreate(BaseModel):
    firm_name: str
    access_code: str
    vat_rate_default: Decimal = Decimal("10")


class Account(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    firm_name: str
    subscription_status: str
    vat_rate_default: Decimal
    created_at: datetime


class AccessCodeRedeem(BaseModel):
    access_code: str


class CheckoutSessionCreate(BaseModel):
    success_url: str
    cancel_url: str


class CheckoutSessionOut(BaseModel):
    checkout_url: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    account: Account
    role: str = "owner"


class SessionInfo(BaseModel):
    account: Account
    role: str


class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: Literal["owner", "editor"] = "editor"


class LoginRequest(BaseModel):
    email: str
    password: str


class User(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    name: str
    email: str
    role: str


class PaymentTerm(BaseModel):
    pct: float
    label: str


class DocTemplateCreate(BaseModel):
    name: str = "Default"
    layout_key: str = "default"
    logo_asset_path: Optional[str] = None
    primary_color: str = "#2B2B2B"
    accent_color: str = "#C9A24B"
    show_tag_column: bool = False
    default_payment_terms: list[PaymentTerm] = Field(
        default_factory=lambda: [PaymentTerm(**t) for t in DEFAULT_PAYMENT_TERMS]
    )
    is_default: bool = True


class DocTemplateUpdate(BaseModel):
    name: Optional[str] = None
    layout_key: Optional[str] = None
    logo_asset_path: Optional[str] = None
    primary_color: Optional[str] = None
    accent_color: Optional[str] = None
    show_tag_column: Optional[bool] = None
    default_payment_terms: Optional[list[PaymentTerm]] = None
    is_default: Optional[bool] = None


class DocTemplate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    name: str
    layout_key: str
    logo_asset_path: Optional[str]
    logo_url: Optional[str] = None
    primary_color: str
    accent_color: str
    show_tag_column: bool
    default_payment_terms: list[dict]
    is_default: bool


class TermsSection(BaseModel):
    heading: Optional[str] = None
    body: str


class TermsTemplateCreate(BaseModel):
    name: str = "Standard Terms"
    sections: list[TermsSection] = Field(default_factory=list)
    is_default: bool = True


class TermsTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    name: str
    sections: list[dict]
    is_default: bool


# --- Categories / catalog ---------------------------------------------------------

class CategoryCreate(BaseModel):
    code: str
    name: str
    two_letter_code: str
    process_sequence: int


class Category(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: Optional[str]
    code: str
    name: str
    two_letter_code: str
    process_sequence: int


class CatalogItemCreate(BaseModel):
    category_id: str
    sub_category: Optional[str] = None
    tag: Optional[str] = None
    description: str
    default_unit: str = "Item"
    dimensions: Optional[str] = None
    material: Optional[str] = None
    finish: Optional[str] = None
    manufacturer: Optional[str] = None
    supplier_unit_cost: Optional[Decimal] = None
    client_unit_price: Optional[Decimal] = None
    delivery_timeline: Optional[str] = None
    installation_requirement: Optional[str] = None
    warranty_years: Optional[int] = None
    sustainability_cert: Optional[str] = None
    maintenance_notes: Optional[str] = None


class CatalogItemUpdate(BaseModel):
    category_id: Optional[str] = None
    sub_category: Optional[str] = None
    tag: Optional[str] = None
    description: Optional[str] = None
    default_unit: Optional[str] = None
    dimensions: Optional[str] = None
    material: Optional[str] = None
    finish: Optional[str] = None
    manufacturer: Optional[str] = None
    supplier_unit_cost: Optional[Decimal] = None
    client_unit_price: Optional[Decimal] = None
    delivery_timeline: Optional[str] = None
    installation_requirement: Optional[str] = None
    warranty_years: Optional[int] = None
    sustainability_cert: Optional[str] = None
    maintenance_notes: Optional[str] = None
    is_active: Optional[bool] = None


class CatalogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    category_id: str
    sub_category: Optional[str]
    tag: Optional[str]
    description: str
    default_unit: str
    dimensions: Optional[str]
    material: Optional[str]
    finish: Optional[str]
    manufacturer: Optional[str]
    supplier_unit_cost: Optional[Decimal]
    client_unit_price: Optional[Decimal]
    delivery_timeline: Optional[str]
    installation_requirement: Optional[str]
    warranty_years: Optional[int]
    sustainability_cert: Optional[str]
    maintenance_notes: Optional[str]
    is_active: bool
    profit: Optional[Decimal] = None
    margin_pct: Optional[Decimal] = None


# --- Projects / rooms ---------------------------------------------------------

class ProjectCreate(BaseModel):
    project_name: str
    project_number: Optional[str] = None
    client_name: str
    client_contact: Optional[str] = None
    location: Optional[str] = None


class Project(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    project_number: Optional[str]
    project_name: str
    client_name: str
    client_contact: Optional[str]
    location: Optional[str]
    created_at: datetime


class RoomCreate(BaseModel):
    floor_name: str
    room_name: str
    room_tag: Optional[str] = None
    area_sqm: Optional[Decimal] = None
    perimeter_mm: Optional[Decimal] = None
    ceiling_type: Optional[str] = None
    wall_finish: Optional[str] = None
    floor_finish: Optional[str] = None


class Room(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    floor_name: str
    room_name: str
    room_tag: Optional[str]
    area_sqm: Optional[Decimal]
    perimeter_mm: Optional[Decimal]
    ceiling_type: Optional[str]
    wall_finish: Optional[str]
    floor_finish: Optional[str]


# --- Quotes ---------------------------------------------------------------------

class QuoteCreate(BaseModel):
    project_id: str
    client_name: str
    quote_number: Optional[str] = None
    vat_rate: Optional[Decimal] = None            # defaults to account.vat_rate_default
    payment_terms: Optional[list[PaymentTerm]] = None  # defaults to doc_template.default_payment_terms
    doc_template_id: Optional[str] = None          # defaults to account's default template


class QuoteLineItemCreate(BaseModel):
    catalog_item_id: Optional[str] = None   # if set, description/unit/tag/prices are copied from catalog
    category_id: Optional[str] = None       # required if catalog_item_id is not set
    description: Optional[str] = None
    unit: Optional[str] = None
    quantity: Decimal = Decimal("1")
    client_unit_price: Optional[Decimal] = None
    supplier_unit_cost: Optional[Decimal] = None
    tag: Optional[str] = None
    room_id: Optional[str] = None


class QuoteLineItemUpdate(BaseModel):
    description: Optional[str] = None
    unit: Optional[str] = None
    quantity: Optional[Decimal] = None
    client_unit_price: Optional[Decimal] = None
    supplier_unit_cost: Optional[Decimal] = None
    room_id: Optional[str] = None
    sort_order: Optional[int] = None


class QuoteLineItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    quote_id: str
    catalog_item_id: Optional[str]
    category_id: str
    tag: Optional[str]
    description: str
    unit: str
    quantity: Decimal
    client_unit_price: Decimal
    line_total: Decimal
    room_id: Optional[str]
    sort_order: int


class QuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: str
    project_id: str
    quote_number: Optional[str]
    client_name: str
    status: str
    vat_rate_snapshot: Decimal
    payment_terms: list[dict]
    doc_template_id: Optional[str]
    created_at: datetime
    share_token: Optional[str]
    accepted_at: Optional[datetime]
    accepted_by_name: Optional[str]


class PaymentScheduleRow(BaseModel):
    pct: float
    label: str
    amount: Decimal


class CategoryGroupOut(BaseModel):
    category_id: str
    category_name: str
    process_sequence: int
    items: list[QuoteLineItemOut]
    category_subtotal: Decimal


class QuoteCalculateResponse(BaseModel):
    quote_id: str
    groups: list[CategoryGroupOut]
    subtotal: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    total: Decimal
    payment_schedule: list[PaymentScheduleRow]


# --- v2 estimation aids (Rate Library / cost build-up / Fit-Out Benchmarks) ------

class RateLibraryEntryCreate(BaseModel):
    area: Optional[str] = None
    category: Optional[str] = None
    item_description: str
    unit: Optional[str] = None
    supplier_cost: Optional[Decimal] = None
    client_unit_price: Optional[Decimal] = None
    rate_basis: Literal["unit_rate", "lump_sum", "tbc"] = "unit_rate"
    source_note: Optional[str] = None


class RateLibraryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: Optional[str]
    area: Optional[str]
    category: Optional[str]
    item_description: str
    unit: Optional[str]
    supplier_cost: Optional[Decimal]
    client_unit_price: Optional[Decimal]
    rate_basis: str
    source_note: Optional[str]
    imported_at: Optional[datetime]


class CostBuildupDefaultsUpdate(BaseModel):
    wastage_pct: Optional[Decimal] = None
    overhead_pct: Optional[Decimal] = None
    margin_pct: Optional[Decimal] = None


class CostBuildupDefaultsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    account_id: str
    wastage_pct: Decimal
    overhead_pct: Decimal
    margin_pct: Decimal


class CostBuildupCalculateRequest(BaseModel):
    material_cost_per_unit: Decimal = Decimal("0")
    labour_cost_per_unit: Decimal = Decimal("0")
    quantity: Decimal = Decimal("1")
    wastage_pct: Optional[Decimal] = None   # overrides the account default when set
    overhead_pct: Optional[Decimal] = None
    margin_pct: Optional[Decimal] = None


class CostBuildupCalculateResponse(BaseModel):
    wastage: Decimal
    prime_cost: Decimal
    overhead: Decimal
    total_cost: Decimal
    margin: Decimal
    proposed_unit_rate: Decimal
    proposed_total: Decimal


class FitOutBenchmark(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    account_id: Optional[str]
    project_type: str
    trade_element: str
    low_bhd_per_sqm: Decimal
    high_bhd_per_sqm: Decimal


# --- workbook import ---------------------------------------------------------

class WorkbookImportResponse(BaseModel):
    project: Project
    quote: QuoteOut
    line_items_imported: int
    catalog_items_created: int
    catalog_items_updated: int


# --- public (client-facing) quote view ---------------------------------------

class PublicQuoteLineItem(BaseModel):
    description: str
    unit: str
    quantity: Decimal
    line_total: Decimal


class PublicCategoryGroup(BaseModel):
    category_name: str
    items: list[PublicQuoteLineItem]
    category_subtotal: Decimal


class PublicQuoteView(BaseModel):
    firm_name: str
    logo_url: Optional[str]
    primary_color: str
    accent_color: str
    project_name: str
    client_name: str
    quote_number: Optional[str]
    status: str
    groups: list[PublicCategoryGroup]
    subtotal: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    total: Decimal
    payment_schedule: list[PaymentScheduleRow]
    terms_sections: list[dict]
    accepted_at: Optional[datetime]
    accepted_by_name: Optional[str]


class QuoteAcceptRequest(BaseModel):
    accepted_by_name: str
