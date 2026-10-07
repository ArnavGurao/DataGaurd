"""Password hashing, JWT access tokens and owner scoping (README 9.6).

Access tokens carry the user id in ``sub`` and expire in 30 minutes by default.
Rights are checked by querying by *both* resource id and owner id, so another
user's resource is reported as 404 rather than 403 — its existence is not
disclosed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .errors import CODE_UNAUTHORIZED, ApiError
from .models import User

#: Argon2id via the recommended pwdlib configuration.
_password_hash = PasswordHash.recommended()

#: auto_error=False so we can raise our own shaped 401 instead of FastAPI's.
_bearer_scheme = HTTPBearer(auto_error=False)

_TOKEN_USE_ACCESS = "access"
_TOKEN_USE_DOWNLOAD = "download"


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        # A malformed stored hash must read as "wrong password", never crash.
        return False


def normalize_email(email: str) -> str:
    """Trim and lowercase so uniqueness is enforced on one canonical form."""
    return email.strip().lower()


def create_access_token(user_id: uuid.UUID, *, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    minutes = settings.jwt_expire_minutes if expires_minutes is None else expires_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "token_use": _TOKEN_USE_ACCESS,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, expected_use: str) -> dict:
    """Verify signature, expiry and intended use. Raises ``ApiError`` 401."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            # Explicit allow-list: never let the token choose its own algorithm.
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(401, CODE_UNAUTHORIZED, "Your session expired. Sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, CODE_UNAUTHORIZED, "Invalid authentication token.") from exc

    if payload.get("token_use") != expected_use:
        raise ApiError(401, CODE_UNAUTHORIZED, "Invalid authentication token.")
    return payload


def create_download_token(
    *, user_id: uuid.UUID, job_id: uuid.UUID, kind: str, ttl_seconds: int
) -> str:
    """A short-lived token for one artifact of one job.

    Local mode has no S3 presigner, so the download URL points back at this API
    carrying this token. It is scoped to a single artifact and expires, so it
    behaves like a presigned URL without weakening the ownership check
    (README 10.6, 12.3).
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "job_id": str(job_id),
        "kind": kind,
        "token_use": _TOKEN_USE_DOWNLOAD,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_download_token(token: str) -> dict:
    return decode_token(token, expected_use=_TOKEN_USE_DOWNLOAD)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the caller from the validated token."""
    if credentials is None or not credentials.credentials:
        raise ApiError(401, CODE_UNAUTHORIZED, "Authentication required.")

    payload = decode_token(credentials.credentials, expected_use=_TOKEN_USE_ACCESS)

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError) as exc:
        raise ApiError(401, CODE_UNAUTHORIZED, "Invalid authentication token.") from exc

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        # Token is well-formed but the account is gone.
        raise ApiError(401, CODE_UNAUTHORIZED, "Invalid authentication token.")
    return user
