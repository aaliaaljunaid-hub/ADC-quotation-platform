"""
Session auth: an access code or a per-user email/password login (see
app/main.py's /auth/redeem and /auth/login) both redeem for a signed JWT
(subject = account_id) — a session is account-wide either way, since even a
per-user login just proves "this person belongs to this account", not a
separate per-user resource scope. Every account-scoped/resource-scoped
endpoint requires that token and checks the caller actually owns the
resource. Without this, any client that knew or guessed another firm's UUID
could read or write their catalog, quotes, and branding — the account_id
path segment alone proved nothing.

The token also carries a "role" claim ("owner" | "editor"). An access-code
login always gets "owner" (only someone with the firm's admin credential
could redeem it); a per-user login gets that user's own role. Most actions
don't check role at all — only the handful gated by require_owner below.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.db_models import Account

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=7)

_DEV_SECRET = "dev-insecure-secret-change-me"
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", _DEV_SECRET)
if SECRET_KEY == _DEV_SECRET:
    logging.getLogger(__name__).warning(
        "JWT_SECRET_KEY is not set — using an insecure development default. "
        "Set a real secret before deploying."
    )


def create_access_token(account_id: str, role: str = "owner") -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": account_id, "role": role, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired, please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid session token")


def get_current_account(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> Account:
    payload = _decode_token(authorization)
    account = db.get(Account, payload.get("sub"))
    if account is None:
        raise HTTPException(401, "Account no longer exists")
    return account


def get_current_role(authorization: str | None = Header(default=None)) -> str:
    """A token predating the role claim (there shouldn't be any in practice —
    tokens expire after 7 days and this feature shipped before any real
    deployment) is treated as "owner", matching the access-code default."""
    payload = _decode_token(authorization)
    return payload.get("role", "owner")


def require_owner(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> Account:
    payload = _decode_token(authorization)
    account = db.get(Account, payload.get("sub"))
    if account is None:
        raise HTTPException(401, "Account no longer exists")
    if payload.get("role", "owner") != "owner":
        raise HTTPException(403, "Only an account owner can do this.")
    return account


def require_account_match(account_id: str, current: Account) -> None:
    """Raise 403 if the path's account_id isn't the authenticated account."""
    if account_id != current.id:
        raise HTTPException(403, "Not authorized for this account")
