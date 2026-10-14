"""Email + password auth: bcrypt hashes, short-lived HS256 JWT carried in an httpOnly cookie
(or Authorization: Bearer for API clients), server-side session rows for revocation."""
from __future__ import annotations
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import AuthSession, User

JWT_SECRET = os.getenv("JWT_SECRET") or "dev-only-change-me-" + "x" * 32
JWT_ALG = "HS256"
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
COOKIE_NAME = "opschk_session"   # name kept for session compatibility
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


_DUMMY_HASH = hash_password("timing-equalizer")


def authenticate(db: Session, email: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user:
        verify_password(password, _DUMMY_HASH)     # constant-ish time for unknown emails
        return None
    if not user.active or not verify_password(password, user.password_hash):
        return None
    return user


def issue_token(db: Session, user: User, user_agent: str | None = None) -> tuple[str, datetime]:
    jti = secrets.token_urlsafe(24)
    exp = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    db.add(AuthSession(id=jti, user_id=user.id, company_id=user.company_id, expires_at=exp, user_agent=(user_agent or "")[:300]))
    token = jwt.encode({"sub": str(user.id), "cid": user.company_id, "role": user.role, "name": user.full_name,
                        "jti": jti, "exp": exp, "iat": datetime.now(timezone.utc)}, JWT_SECRET, algorithm=JWT_ALG)
    return token, exp


def _token_from_request(request: Request) -> Optional[str]:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def current_session(request: Request, db: Session = Depends(get_db)) -> tuple[User, AuthSession]:
    token = _token_from_request(request)
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token")
    sess = db.get(AuthSession, payload.get("jti"))
    if not sess or sess.revoked_at or sess.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, "Session revoked or expired")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active or user.company_id != sess.company_id:
        raise HTTPException(401, "User inactive")
    return user, sess


# Paths usable before the current Terms version is accepted (everything else returns 403 terms_required).
TERMS_EXEMPT = {"/api/auth/me", "/api/auth/logout", "/api/auth/accept-terms",
                "/api/account", "/api/account/export", "/api/account/delete"}   # privacy rights never need new Terms
# Paths usable while the company workspace is scheduled for deletion (everything else returns 423).
DELETION_EXEMPT = TERMS_EXEMPT | {"/api/company/delete/cancel"}


def current_user(request: Request, s=Depends(current_session)) -> User:
    from . import config
    user = s[0]
    if user.company.deletion_scheduled_for and request.url.path not in DELETION_EXEMPT:
        raise HTTPException(423, {"code": "workspace_pending_deletion", "scheduled_for": user.company.deletion_scheduled_for.isoformat(),
                                  "message": "This workspace is scheduled for permanent deletion. A manager can cancel it under Account; "
                                             "everyone can still export their data until then."})
    if user.terms_version != config.TERMS_VERSION and request.url.path not in TERMS_EXEMPT:
        raise HTTPException(403, {"code": "terms_required", "version": config.TERMS_VERSION,
                                  "message": "Please review and accept the current Terms of Service and Privacy Policy to continue."})
    return user


def require_manager(user: User = Depends(current_user)) -> User:
    if user.role not in ("manager", "admin"):
        raise HTTPException(403, "Manager role required")
    return user


def require_technician(user: User = Depends(current_user)) -> User:
    if not user.technician_id:
        raise HTTPException(403, "This account is not linked to a technician record")
    return user
