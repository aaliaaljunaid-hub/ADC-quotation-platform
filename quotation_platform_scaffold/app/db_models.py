"""
SQLAlchemy models for the catalog-driven quotation platform.

Replaces the old PricingTier/FeeMethod tiered-pricing model: pricing here is
just quantity x catalog_item.client_unit_price, grouped by category in
categories.process_sequence order (mirrors the ADC/Consilio sheets' Z/AA
header-detection logic, done as a GROUP BY instead of helper columns).
"""
from __future__ import annotations
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
)
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Account(Base):
    __tablename__ = "accounts"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    firm_name = Column(String, nullable=False)
    access_code_hash = Column(String, nullable=False)
    subscription_status = Column(String, default="trial")  # trial | active | past_due | canceled
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    vat_rate_default = Column(Numeric(5, 2), default=10)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="account", cascade="all, delete-orphan")
    categories = relationship("Category", back_populates="account", cascade="all, delete-orphan")
    catalog_items = relationship("CatalogItem", back_populates="account", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="account", cascade="all, delete-orphan")
    doc_templates = relationship("DocTemplate", back_populates="account", cascade="all, delete-orphan")
    terms_templates = relationship("TermsTemplate", back_populates="account", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="editor")  # owner | editor
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account", back_populates="users")


class Category(Base):
    """Global defaults have account_id = NULL (the 35 ADC categories, seeded
    from the Tag Codes / Lists sheets). An account can add its own on top."""

    __tablename__ = "categories"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=True)
    code = Column(String(4), nullable=False)              # "01"
    name = Column(String, nullable=False)                 # "Design"
    two_letter_code = Column(String(2), nullable=False)   # "DS" -> tag prefix
    process_sequence = Column(Integer, nullable=False)    # print order in the quote

    account = relationship("Account", back_populates="categories")
    catalog_items = relationship("CatalogItem", back_populates="category")


class CatalogItem(Base):
    """The account's item catalog (equivalent of '2.FF & E')."""

    __tablename__ = "catalog_items"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    category_id = Column(String(36), ForeignKey("categories.id"), nullable=False)
    sub_category = Column(String, nullable=True)
    tag = Column(String, nullable=True)                   # e.g. "DS-01"
    description = Column(Text, nullable=False)
    default_unit = Column(String, default="Item")
    dimensions = Column(String, nullable=True)
    material = Column(String, nullable=True)
    finish = Column(String, nullable=True)
    manufacturer = Column(String, nullable=True)
    supplier_unit_cost = Column(Numeric(12, 3), nullable=True)
    client_unit_price = Column(Numeric(12, 3), nullable=True)
    delivery_timeline = Column(String, nullable=True)
    installation_requirement = Column(String, nullable=True)
    warranty_years = Column(Integer, nullable=True)
    sustainability_cert = Column(String, nullable=True)
    maintenance_notes = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account", back_populates="catalog_items")
    category = relationship("Category", back_populates="catalog_items")

    @property
    def profit(self):
        if self.supplier_unit_cost is None or self.client_unit_price is None:
            return None
        return self.client_unit_price - self.supplier_unit_cost

    @property
    def margin_pct(self):
        p = self.profit
        if p is None or not self.client_unit_price:
            return None
        return p / self.client_unit_price


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    project_number = Column(String, nullable=True)
    project_name = Column(String, nullable=False)
    client_name = Column(String, nullable=False)
    client_contact = Column(String, nullable=True)
    location = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account", back_populates="projects")
    floors = relationship("ProjectFloor", back_populates="project", cascade="all, delete-orphan")
    rooms = relationship("Room", back_populates="project", cascade="all, delete-orphan")
    quotes = relationship("Quote", back_populates="project", cascade="all, delete-orphan")


class ProjectFloor(Base):
    __tablename__ = "project_floors"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    floor_name = Column(String, nullable=False)
    area_sqm = Column(Numeric(10, 2), nullable=True)

    project = relationship("Project", back_populates="floors")


class Room(Base):
    __tablename__ = "rooms"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    floor_name = Column(String, nullable=False)
    room_name = Column(String, nullable=False)
    room_tag = Column(String, nullable=True)              # e.g. "RM-GF-01"
    area_sqm = Column(Numeric(10, 2), nullable=True)
    perimeter_mm = Column(Numeric(10, 2), nullable=True)
    ceiling_type = Column(String, nullable=True)
    wall_finish = Column(String, nullable=True)
    floor_finish = Column(String, nullable=True)

    project = relationship("Project", back_populates="rooms")


class DocTemplate(Base):
    """Per-account branding. layout_key + show_tag_column reproduce the
    ADC vs Consilio vs '-Tag' variants as one toggle-able template."""

    __tablename__ = "doc_templates"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    name = Column(String, default="Default")
    layout_key = Column(String, default="default")
    logo_asset_path = Column(String, nullable=True)
    primary_color = Column(String, default="#2B2B2B")   # charcoal
    accent_color = Column(String, default="#C9A24B")     # gold
    show_tag_column = Column(Boolean, default=False)
    default_payment_terms = Column(JSON, default=list)   # [{"pct": 70, "label": "..."}]
    is_default = Column(Boolean, default=True)

    account = relationship("Account", back_populates="doc_templates")

    @property
    def logo_url(self):
        """Browser-servable URL for the uploaded logo, via whichever asset
        storage backend is configured (local disk or S3 — see app/storage.py)."""
        if not self.logo_asset_path:
            return None
        from app.storage import get_asset_storage

        return get_asset_storage().url_for(self.logo_asset_path)


class TermsTemplate(Base):
    __tablename__ = "terms_templates"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    name = Column(String, default="Standard Terms")
    sections = Column(JSON, default=list)  # [{"heading": "...", "body": "..."}]
    is_default = Column(Boolean, default=True)

    account = relationship("Account", back_populates="terms_templates")


class Quote(Base):
    __tablename__ = "quotes"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    quote_number = Column(String, nullable=True)
    client_name = Column(String, nullable=False)
    status = Column(String, default="draft")  # draft | sent | accepted | expired
    vat_rate_snapshot = Column(Numeric(5, 2), default=10)
    payment_terms = Column(JSON, default=list)
    doc_template_id = Column(String(36), ForeignKey("doc_templates.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    # Public client-facing link: an opaque, unguessable token (not a JWT —
    # the client has no account) granting read + accept access to exactly
    # this one quote, nothing else. Generated at quote creation.
    share_token = Column(String(64), unique=True, nullable=True, index=True)
    accepted_at = Column(DateTime, nullable=True)
    accepted_by_name = Column(String, nullable=True)  # lightweight typed "signature"

    account = relationship("Account")
    project = relationship("Project", back_populates="quotes")
    doc_template = relationship("DocTemplate")
    line_items = relationship(
        "QuoteLineItem",
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteLineItem.sort_order",
    )


class QuoteLineItem(Base):
    """Catalog fields are snapshotted here at add-time so a sent quote never
    silently reprices when the master catalog changes later."""

    __tablename__ = "quote_line_items"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    quote_id = Column(String(36), ForeignKey("quotes.id"), nullable=False)
    catalog_item_id = Column(String(36), ForeignKey("catalog_items.id"), nullable=True)
    category_id = Column(String(36), ForeignKey("categories.id"), nullable=False)
    tag = Column(String, nullable=True)
    description = Column(Text, nullable=False)
    unit = Column(String, nullable=False, default="Item")
    quantity = Column(Numeric(12, 3), nullable=False, default=1)
    client_unit_price = Column(Numeric(12, 3), nullable=False, default=0)
    supplier_unit_cost = Column(Numeric(12, 3), nullable=True)  # internal-only, never printed
    room_id = Column(String(36), ForeignKey("rooms.id"), nullable=True)
    sort_order = Column(Integer, default=0)

    quote = relationship("Quote", back_populates="line_items")
    category = relationship("Category")
    catalog_item = relationship("CatalogItem")
    room = relationship("Room")

    @property
    def line_total(self):
        return (self.quantity or 0) * (self.client_unit_price or 0)


# --- v2 estimation aids -------------------------------------------------------------
# Traced from the source workbook's 'Rate Library', 'Rate Build-Up' and
# 'Fit-Out Benchmarks' sheets. None of these feed the quote-generation
# critical path automatically — they're reference/estimation tools a
# designer consults while pricing a catalog_item, matching how the original
# sheets worked (frozen historical rates, a manual cost calculator, and an
# indicative BHD/m² sense-check).

class RateLibraryEntry(Base):
    """Frozen historical reference rates — not live-linked to anything, may
    contain unpriced/TBC rows and deliberate duplicates from different
    sources, exactly like the source 'Rate Library' sheet."""

    __tablename__ = "rate_library_entries"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=True)  # null = shared library
    area = Column(String, nullable=True)             # e.g. "Flooring"
    category = Column(String, nullable=True)          # e.g. "Concrete"
    item_description = Column(Text, nullable=False)
    unit = Column(String, nullable=True)
    supplier_cost = Column(Numeric(12, 3), nullable=True)
    client_unit_price = Column(Numeric(12, 3), nullable=True)
    rate_basis = Column(String, default="unit_rate")  # unit_rate | lump_sum | tbc
    source_note = Column(String, nullable=True)
    imported_at = Column(DateTime, nullable=True)


class CostBuildupDefaults(Base):
    """Per-account wastage/overhead/margin assumptions backing the cost
    build-up calculator (one row per account, get-or-create)."""

    __tablename__ = "cost_buildup_defaults"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False, unique=True)
    wastage_pct = Column(Numeric(5, 2), default=5)
    overhead_pct = Column(Numeric(5, 2), default=10)
    margin_pct = Column(Numeric(5, 2), default=15)


class FitOutBenchmark(Base):
    """Indicative all-in fit-out cost bands (BHD per m² of floor area) by
    project type and trade element — a sense-check only, never wired into
    pricing math."""

    __tablename__ = "fitout_benchmarks"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=True)  # null = global default
    project_type = Column(String, nullable=False)     # Office | Retail | Restaurant | ...
    trade_element = Column(String, nullable=False)     # MEP | Partitions | Flooring | Ceiling | Joinery & finishes
    low_bhd_per_sqm = Column(Numeric(10, 2), nullable=False)
    high_bhd_per_sqm = Column(Numeric(10, 2), nullable=False)
