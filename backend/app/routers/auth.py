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
from ..schemas import LoginRequest, RegisterRequest, TokenOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
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
    return user


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenOut:
    email = normalize_email(payload.email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    # One message for both outcomes so the endpoint cannot be used to discover
    # which addresses are registered.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise ApiError(401, CODE_UNAUTHORIZED, "Incorrect email or password.")

    return TokenOut(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
