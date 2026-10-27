"""Registration, login and identity (README 9.6, 9.7)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import create_access_token, get_current_user, hash_password, normalize_email, verify_password
from ..database import get_db
from ..errors import CODE_CONFLICT, CODE_UNAUTHORIZED, ApiError
from ..models import User
from ..config import get_settings
from ..services.verification import issue_code, consume_code
from ..schemas import VerificationRequest, ResendVerificationRequest
import logging
from ..schemas import LoginRequest, RegisterRequest, TokenOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = normalize_email(payload.email)

    existing = db.execute(select(User.id).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise ApiError(409, CODE_CONFLICT, "An account with that email already exists.")

    user = User(id=uuid.uuid4(), email=email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two concurrent registrations for the same address: the unique index
        # is the real guard, this just reports it as a conflict.
        db.rollback()
        raise ApiError(409, CODE_CONFLICT, "An account with that email already exists.") from exc

    db.refresh(user)
    required = get_settings().email_verification_required
    delivery = None
    if required:
        try:
            user = db.scalar(select(User).where(User.id == user.id).with_for_update())
            issue_code(user)
            db.commit()
            delivery = "sent"
        except Exception:
            db.rollback()
            delivery = "unavailable"
            logging.getLogger(__name__).warning("Verification delivery failed; user can request a new code.")
    return UserOut(id=user.id, email=user.email, email_verification_required=required,
                   email_verified_at=user.email_verified_at, verification_delivery=delivery)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenOut:
    email = normalize_email(payload.email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    # One message for both outcomes so the endpoint cannot be used to discover
    # which addresses are registered.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise ApiError(401, CODE_UNAUTHORIZED, "Incorrect email or password.")

    if get_settings().email_verification_required and not user.email_verified_at:
        raise ApiError(403, "EMAIL_NOT_VERIFIED", "Verify your email before signing in. Request a code if needed.")

    return TokenOut(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/verify-email")
def verify_email(payload: VerificationRequest, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == normalize_email(payload.email)).with_for_update()).scalar_one_or_none()
    valid = bool(user and consume_code(user, payload.code))
    db.commit()  # Failed attempts must also persist.
    if not valid:
        raise ApiError(422, "INVALID_VERIFICATION_CODE", "Invalid or expired code. Request a new code if needed.")
    return {"message": "Email verified. You can now sign in."}


@router.post("/resend-verification")
def resend_verification(payload: ResendVerificationRequest, db: Session = Depends(get_db)):
    if get_settings().email_verification_required:
        user = db.execute(select(User).where(User.email == normalize_email(payload.email)).with_for_update()).scalar_one_or_none()
        if user and not user.email_verified_at:
            try:
                issue_code(user)
                db.commit()
            except Exception:
                db.rollback()
                logging.getLogger(__name__).warning("Verification resend delivery failed.")
    return {"message": "If this account needs verification, a code will be sent. Wait 60 seconds before requesting another."}
