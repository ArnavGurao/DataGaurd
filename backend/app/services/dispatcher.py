"""Durable job dispatch: queue adapters and the API-side publisher (README 13.2, 13.3).

An RDS commit and a queue send are not one transaction, so the committed
``PENDING_DISPATCH`` row *is* the durable dispatch record. The publisher sends
first and only then flips the row to ``QUEUED``, conditionally, so a crash
between the two just causes a harmless duplicate message that the worker
tolerates. One EC2 instance runs one API process, hence one publisher.
"""

from __future__ import annotations

import logging
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update

from ..config import get_settings
from ..database import session_scope
from ..models import Job, JobStatus

logger = logging.getLogger("dataguard.dispatcher")


@dataclass(frozen=True)
class QueueMessage:
    """A unit of work handed to the worker."""

    job_id: uuid.UUID
    #: SQS receipt handle. ``None`` in database mode, where the row is the message.
    receipt_handle: str | None = None
    #: SQS approximate receive count, an operational signal rather than the
    #: authoritative attempt counter, which lives on the job row.
    receive_count: int | None = None


class JobQueue(ABC):
    """The seam between job dispatch and the transport carrying it."""

    @abstractmethod
    def send(self, job_id: uuid.UUID) -> None:
        """Make a pending job available to workers."""

    @abstractmethod
    def receive(self, *, wait_seconds: int, max_messages: int) -> list[QueueMessage]:
        """Long-poll for work. Returns an empty list when nothing arrives."""

    @abstractmethod
    def delete(self, message: QueueMessage) -> None:
        """Acknowledge a message after its outcome is committed to the database."""


class DatabaseJobQueue(JobQueue):
    """Local stand-in: the ``jobs`` table is the queue (README 13.2).

    This exists so process separation is real — the API and worker are separate
    processes talking through PostgreSQL, not an in-memory list.
    """

    def send(self, job_id: uuid.UUID) -> None:
        # The committed row is already the durable message; the publisher's
        # status flip is what makes it visible. Nothing to transmit.
        return

    def receive(self, *, wait_seconds: int, max_messages: int) -> list[QueueMessage]:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            job_ids = (
                session.execute(
                    select(Job.id)
                    .where(
                        or_(
                            Job.status == JobStatus.PENDING_DISPATCH.value,
                            # QUEUED, but only once any retry not-before has
                            # passed. The worker parks a retried job on
                            # lease_until to give a transient failure time to
                            # clear; without honouring it the row would be
                            # picked straight back up (README 13.5).
                            and_(
                                Job.status == JobStatus.QUEUED.value,
                                or_(Job.lease_until.is_(None), Job.lease_until <= now),
                            ),
                            # A RUNNING row whose lease has expired was left
                            # behind by a worker that died mid-run. It must be
                            # delivered again or nothing can reclaim it:
                            # database mode has no visibility timeout and no
                            # lease reaper, so the existing claim-side reclaim is
                            # otherwise unreachable (README 13.8). A row with a
                            # LIVE lease is still left for its owner, which is
                            # what keeps the busy_duplicate path intact.
                            and_(
                                Job.status == JobStatus.RUNNING.value,
                                or_(Job.lease_until.is_(None), Job.lease_until <= now),
                            ),
                        )
                    )
                    .order_by(Job.created_at)
                    .limit(max_messages)
                )
                .scalars()
                .all()
            )
        # Blocking waits are the worker's business; this backend just reports.
        return [QueueMessage(job_id=job_id) for job_id in job_ids]

    def delete(self, message: QueueMessage) -> None:
        # Acknowledged by the status transition committed during completion.
        return


class SqsJobQueue(JobQueue):
    """AWS SQS implementation — owner C fills this in (README 13.3).

    Boto3 takes temporary credentials from the EC2 instance role. Configure
    bounded connection/read timeouts and a small retry budget so a hung SQS
    call cannot stall the worker indefinitely.
    """

    #: Long poll, as specified in README 13.4 step 1.
    DEFAULT_WAIT_SECONDS = 20

    def __init__(self, queue_url: str | None = None, region: str | None = None) -> None:
        settings = get_settings()
        self._queue_url = queue_url or settings.sqs_queue_url
        self._region = region or settings.aws_region
        if not self._queue_url:
            raise RuntimeError("SQS_QUEUE_URL must be set when QUEUE_MODE=sqs.")

    @property
    def queue_url(self) -> str:
        return self._queue_url  # type: ignore[return-value]

    def _client(self):
        import boto3  # lazy: local development needs no AWS session

        return boto3.client("sqs", region_name=self._region)

    def send(self, job_id: uuid.UUID) -> None:
        raise NotImplementedError(
            "Owner C: send_message with only the job_id as the body (README 13.3 step 4)."
        )

    def receive(self, *, wait_seconds: int = DEFAULT_WAIT_SECONDS, max_messages: int = 1) -> list[QueueMessage]:
        raise NotImplementedError(
            "Owner C: receive_message with WaitTimeSeconds=20 and MaxNumberOfMessages=1; "
            "parse and validate the message body's job_id, and never trust it for paths "
            "or rules — load those from RDS (README 13.4 step 2)."
        )

    def delete(self, message: QueueMessage) -> None:
        raise NotImplementedError(
            "Owner C: delete_message using the receipt handle, and only after the "
            "completion transaction commits (README 13.4 step 9)."
        )


def get_queue() -> JobQueue:
    """Build the configured queue. No silent fallback between modes."""
    settings = get_settings()
    if settings.queue_mode == "sqs":
        return SqsJobQueue()
    return DatabaseJobQueue()


class Publisher:
    """Moves ``PENDING_DISPATCH`` jobs onto the queue and retries notifications.

    Runs in its own daemon thread with its own database sessions so blocking
    database and AWS calls never sit on the API's event loop (README 13.3).
    """

    def __init__(
        self,
        queue: JobQueue | None = None,
        *,
        interval_seconds: float | None = None,
        batch_size: int = 50,
        notifier=None,
    ) -> None:
        self._queue = queue
        self._interval = interval_seconds or get_settings().publisher_interval_seconds
        self._batch_size = batch_size
        self._notifier = notifier
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def queue(self) -> JobQueue:
        if self._queue is None:
            self._queue = get_queue()
        return self._queue

    def run_once(self) -> int:
        """Dispatch one batch. Returns how many jobs moved to QUEUED."""
        job_ids = self._pending_job_ids()
        dispatched = 0

        for job_id in job_ids:
            try:
                self.queue.send(job_id)
            except Exception:
                # Leave the row pending and try again next iteration, with the
                # row's age acting as the backoff signal (README 13.3 step 5).
                logger.warning("Dispatch failed for job %s; leaving it pending.", job_id, exc_info=True)
                continue

            with session_scope() as session:
                result = session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == JobStatus.PENDING_DISPATCH.value)
                    # Conditional on PENDING_DISPATCH so a job that already
                    # reached RUNNING or COMPLETED is never dragged backwards.
                    .values(status=JobStatus.QUEUED.value, last_dispatched_at=func.now())
                )
                dispatched += result.rowcount or 0

        if self._notifier is not None:
            try:
                self._notifier.flush_pending()
            except Exception:
                logger.warning("Notification flush failed.", exc_info=True)
        return dispatched

    def _pending_job_ids(self) -> list[uuid.UUID]:
        with session_scope() as session:
            return list(
                session.execute(
                    select(Job.id)
                    .where(Job.status == JobStatus.PENDING_DISPATCH.value)
                    .order_by(Job.created_at)
                    .limit(self._batch_size)
                )
                .scalars()
                .all()
            )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="dataguard-publisher", daemon=True
        )
        self._thread.start()
        logger.info("Publisher started (interval %.1fs).", self._interval)

    def _loop(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception:
                # A failing iteration must not kill the thread: the next tick
                # is the retry.
                logger.exception("Publisher iteration failed.")
            if self._stop.wait(self._interval):
                return

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("Publisher stopped.")
