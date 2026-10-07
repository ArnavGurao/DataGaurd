"""SQLAlchemy models for the DataGuard schema (README 9.5).

Status and rule-code values are plain strings with check constraints rather
than native PostgreSQL enum types: adding a status later is an ordinary
migration instead of an ``ALTER TYPE`` that cannot run inside a transaction.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class JobStatus(StrEnum):
    """Job lifecycle (README 13.1)."""

    PENDING_DISPATCH = "PENDING_DISPATCH"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


#: Statuses the UI should keep polling (README 10.6).
ACTIVE_JOB_STATUSES: tuple[str, ...] = (
    JobStatus.PENDING_DISPATCH,
    JobStatus.QUEUED,
    JobStatus.RUNNING,
)

#: Statuses where polling stops.
TERMINAL_JOB_STATUSES: tuple[str, ...] = (JobStatus.COMPLETED, JobStatus.FAILED)


class FailureKind(StrEnum):
    """Why a job ended in FAILED, distinguishing input faults from our faults."""

    INPUT = "INPUT"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    INTERNAL = "INTERNAL"


class NotificationEventType(StrEnum):
    """Application-generated operator notifications (README 13.6)."""

    JOB_FAILED = "JOB_FAILED"
    JOB_COMPLETED_WITH_REJECTS = "JOB_COMPLETED_WITH_REJECTS"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Stored lowercase and trimmed; uniqueness is enforced on that form.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    rule_sets: Mapped[list["RuleSet"]] = relationship(back_populates="owner")


class RuleSet(Base):
    __tablename__ = "rule_sets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rules_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="rule_sets")

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_rule_sets_owner_name"),)


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Sanitized display metadata only; never used to build a storage path.
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rule_sets.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    #: Rules frozen at submission: editing the rule set later must not change
    #: the meaning of a finished audit (README 9.5).
    rules_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.PENDING_DISPATCH, server_default="PENDING_DISPATCH"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    artifacts_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dataset: Mapped[Dataset] = relationship()
    rule_set: Mapped[RuleSet] = relationship()

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_DISPATCH', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "failure_kind IS NULL OR failure_kind IN ('INPUT', 'RETRY_EXHAUSTED', 'INTERNAL')",
            name="ck_jobs_failure_kind",
        ),
        # History listing per owner, newest first.
        Index("ix_jobs_owner_created", "owner_id", "created_at"),
        # Worker claim scan and the queued-job-age alarm query.
        Index("ix_jobs_status_lease", "status", "lease_until"),
    )


class NotificationEvent(Base):
    """Durable best-effort SNS publishing (README 13.6).

    A unique (job_id, event_type) row is written in the same transaction that
    completes the job, so a crash before publishing is retryable and an SNS
    outage can never turn a completed audit into FAILED.
    """

    __tablename__ = "notification_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NotificationStatus.PENDING, server_default="PENDING"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "event_type", name="uq_notification_events_job_type"),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED')", name="ck_notification_events_status"
        ),
    )
