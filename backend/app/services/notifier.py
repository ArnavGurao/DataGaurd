"""Operator notifications (README 13.6).

A notification is recorded durably in the same transaction that completes or
fails a job, then delivered on a best-effort basis. Delivery failure must never
change the job's outcome: an SNS outage cannot turn a completed audit into
FAILED.

Bodies carry identifiers and counts only — never CSV contents or credentials.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..database import session_scope
from ..models import Job, JobStatus, NotificationEvent, NotificationEventType, NotificationStatus

logger = logging.getLogger("dataguard.notifier")

#: Bound the retry batch so a backlog cannot stall the publisher thread.
FLUSH_BATCH_SIZE = 25


class Notifier(ABC):
    """The seam between notification intent and the delivery channel."""

    @abstractmethod
    def send(self, event: NotificationEvent, job: Job) -> None:
        """Deliver one notification. Raising marks the event FAILED and retryable."""

    def flush_pending(self) -> int:
        """Retry unsent events. Returns how many were delivered."""
        delivered = 0
        with session_scope() as session:
            events = (
                session.execute(
                    select(NotificationEvent)
                    .where(NotificationEvent.status == NotificationStatus.PENDING.value)
                    .order_by(NotificationEvent.created_at)
                    .limit(FLUSH_BATCH_SIZE)
                )
                .scalars()
                .all()
            )
            payloads = [(event.id, event.event_type, event.job_id) for event in events]

        for event_id, event_type, job_id in payloads:
            with session_scope() as session:
                event = session.get(NotificationEvent, event_id)
                job = session.get(Job, job_id)
                if event is None or job is None:
                    continue
                event.attempts += 1
                try:
                    self.send(event, job)
                except Exception:
                    # Leave it PENDING for the next flush, not FAILED: FAILED is
                    # terminal and would strand a recoverable notice.
                    logger.warning("Notification %s could not be delivered.", event_id, exc_info=True)
                    continue
                event.status = NotificationStatus.SENT.value
                event.sent_at = datetime.now(timezone.utc)
                delivered += 1
        return delivered


class LoggingNotifier(Notifier):
    """Local mode: writes the notice to the application log."""

    def send(self, event: NotificationEvent, job: Job) -> None:
        logger.warning(
            "OPERATOR NOTICE event=%s job_id=%s status=%s attempts=%s",
            event.event_type,
            job.id,
            job.status,
            job.attempts,
        )


class SnsNotifier(Notifier):
    """AWS SNS implementation — owner C fills this in (README 13.6).

    Publishing retries are bounded; on exhaustion the event stays PENDING for
    the next flush.
    """

    def __init__(self, topic_arn: str | None = None, region: str | None = None) -> None:
        settings = get_settings()
        self._topic_arn = topic_arn or settings.sns_topic_arn
        self._region = region or settings.aws_region
        if not self._topic_arn:
            raise RuntimeError("SNS_TOPIC_ARN must be set to use SnsNotifier.")

    @property
    def topic_arn(self) -> str:
        return self._topic_arn  # type: ignore[return-value]

    def _client(self):
        import boto3

        return boto3.client("sns", region_name=self._region)

    def send(self, event: NotificationEvent, job: Job) -> None:
        raise NotImplementedError(
            "Owner C: publish a plain-text operator notice containing the job ID and "
            "event ID so a duplicate delivery after a crash is recognisable."
        )


def get_notifier() -> Notifier:
    settings = get_settings()
    if settings.sns_topic_arn:
        return SnsNotifier()
    return LoggingNotifier()


def event_type_for(job: Job, summary: dict[str, Any] | None) -> str | None:
    """Which notice, if any, this outcome warrants.

    Only failures and completed batches with rejected rows are worth an
    operator's attention; a clean audit is not.
    """
    if job.status == JobStatus.FAILED.value:
        return NotificationEventType.JOB_FAILED.value
    if job.status == JobStatus.COMPLETED.value and summary:
        if int(summary.get("rejected_rows", 0)) > 0:
            return NotificationEventType.JOB_COMPLETED_WITH_REJECTS.value
    return None


def record_event(session, job: Job, event_type: str) -> None:
    """Queue an event inside the caller's transaction.

    Sharing the transaction is the point: the event commits atomically with the
    status change, so a crash before publishing is retryable rather than lost.
    """
    exists = session.execute(
        select(NotificationEvent.id).where(
            NotificationEvent.job_id == job.id,
            NotificationEvent.event_type == event_type,
        )
    ).scalar_one_or_none()
    if exists is not None:
        return  # UNIQUE(job_id, event_type): never queue the same notice twice
    session.add(
        NotificationEvent(
            id=uuid.uuid4(), job_id=job.id, event_type=event_type, status=NotificationStatus.PENDING.value
        )
    )
