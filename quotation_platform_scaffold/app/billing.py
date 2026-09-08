"""
Stripe subscription gating. Two independent pieces:

1. require_active_subscription — a dependency that blocks the paid,
   value-producing actions (building/calculating/generating a quote) once an
   account's subscription has lapsed. Catalog/branding management stays
   available even when lapsed — a canceled account isn't locked out of its
   own data, just out of producing new billable output.

2. Webhook handling — Stripe is the source of truth for subscription state;
   we never set subscription_status from a checkout redirect alone (a user
   can close the tab before the redirect fires). The webhook is what keeps
   account.subscription_status in sync with reality, including payment
   failures and cancellations that don't originate from our own UI at all.
"""
import json
import os

import stripe
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_account
from app.db_models import Account

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

ACTIVE_STATUSES = {"trial", "active"}

# Stripe subscription.status values -> our account.subscription_status values.
_STRIPE_STATUS_MAP = {
    "trialing": "trial",
    "active": "active",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
}


def require_active_subscription(current: Account = Depends(get_current_account)) -> Account:
    if current.subscription_status not in ACTIVE_STATUSES:
        raise HTTPException(
            402,
            f"Subscription is {current.subscription_status}; update billing to continue building quotes.",
        )
    return current


def create_checkout_session(account: Account, success_url: str, cancel_url: str) -> str:
    if not stripe.api_key or not STRIPE_PRICE_ID:
        raise HTTPException(500, "Stripe is not configured on the server (STRIPE_SECRET_KEY / STRIPE_PRICE_ID).")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        customer=account.stripe_customer_id or None,
        client_reference_id=account.id,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"account_id": account.id},
    )
    return session.url


def verify_webhook(payload: bytes, sig_header: str | None) -> dict:
    """Verifies the signature via the SDK, but returns plain-dict JSON rather
    than the SDK's StripeObject — that type's __getattr__ rejects `.get(...)`
    outright ("'get' is a dict method, but a StripeObject is not a dict"),
    which would break every `.get()` call in handle_webhook_event below."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(500, "STRIPE_WEBHOOK_SECRET is not configured on the server.")
    try:
        stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(400, "Invalid webhook payload or signature.")
    return json.loads(payload)


def handle_webhook_event(event: dict, db: Session) -> None:
    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        account_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("account_id")
        account = db.get(Account, account_id) if account_id else None
        if account is not None:
            account.stripe_customer_id = obj.get("customer") or account.stripe_customer_id
            account.stripe_subscription_id = obj.get("subscription") or account.stripe_subscription_id
            account.subscription_status = "active"
            db.commit()
        return

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        account = db.query(Account).filter(Account.stripe_subscription_id == obj["id"]).first()
        if account is not None:
            account.subscription_status = _STRIPE_STATUS_MAP.get(obj["status"], "past_due")
            db.commit()
        return

    if event_type == "customer.subscription.deleted":
        account = db.query(Account).filter(Account.stripe_subscription_id == obj["id"]).first()
        if account is not None:
            account.subscription_status = "canceled"
            db.commit()
        return

    if event_type == "invoice.payment_failed":
        subscription_id = obj.get("subscription")
        account = (
            db.query(Account).filter(Account.stripe_subscription_id == subscription_id).first()
            if subscription_id
            else None
        )
        if account is not None:
            account.subscription_status = "past_due"
            db.commit()
        return
