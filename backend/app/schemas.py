"""Request and response models for the API contract (README 9.7).

Response fields are snake_case throughout, matching the documented examples, so
the frontend never has to guess between ``job_id`` and ``jobId``.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Deliberately dependency-free: pydantic's EmailStr would pull in
# email-validator, which is not in the agreed dependency list (README 4.3).
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


def _validate_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 320 or not _EMAIL_PATTERN.match(email):
        raise ValueError("Enter a valid email address.")
    return email


class RegisterRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _validate_email(value)

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if len(value) > MAX_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
        return value


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _validate_email(value)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RuleSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rules: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Rule-set name must not be blank.")
        return name


class RuleSetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    rules: dict[str, Any]
    created_at: datetime


class SummaryOut(BaseModel):
    total_rows: int
    valid_rows: int
    rejected_rows: int
    valid_percentage: float


class JobErrorOut(BaseModel):
    code: str
    message: str
    failure_kind: str | None = None


class JobOut(BaseModel):
    """The shape returned by the job status, list and upload endpoints.

    ``original_name`` and ``rule_set_name`` are display metadata the frontend
    shows beside a job; both are nullable because a job whose rule set or
    dataset row has since been removed should still render. ``rule_set_id``
    lets the UI link a job back to the checks that produced it.
    """

    job_id: uuid.UUID
    title: str
    original_name: str | None = None
    rule_set_id: uuid.UUID | None = None
    rule_set_name: str | None = None
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: SummaryOut | None = None
    available_downloads: list[str] = Field(default_factory=list)
    error: JobErrorOut | None = None


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class AcceptedJobOut(BaseModel):
    """202 response for an accepted upload (README 9.7)."""

    job_id: uuid.UUID
    status: str


class DownloadUrlOut(BaseModel):
    url: str
    filename: str
    expires_in: int
