"""Background validation worker (README 13.2, 13.4, 13.5).

Run as a separate process::

    .\\.venv\\Scripts\\python.exe -m app.worker

Delivery is **at least once**, never exactly once. Every step is therefore built
to tolerate the same job arriving twice: the claim is a row lock plus an expiring
lease, a duplicate for an already-completed job (or an already-failed INPUT job)
is acknowledged and ignored, and output keys are deterministic so a retry
overwrites rather than accumulates.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from .config import get_settings
from .database import session_scope
from .models import FailureKind, Job, JobStatus
from .services.dispatcher import DatabaseJobQueue, JobQueue, QueueMessage, get_queue
from .services.notifier import (
    NotificationEventType,
    event_type_for,
    get_notifier,
    record_event,
)
from .services.reports import build_rejected_csv, build_report, build_report_json, build_valid_csv
from .services.rules import RuleSet
from .services.storage import StorageError, get_storage, output_key
from .services.validator import (
    ParsedCsv,
    ValidationResult,
    ValidationInputError,
    parse_csv,
    validate,
)

logger = logging.getLogger("dataguard.worker")

#: Matches the SQS redrive maxReceiveCount of 3 (README 13.5).
MAX_ATTEMPTS = 3

#: Long poll, as specified in README 13.4 step 1.
SQS_WAIT_SECONDS = 20

#: How long the loop backs off after an iteration raised before retrying, so a
#: persistent failure cannot spin.
ERROR_BACKOFF_SECONDS = 2.0

#: Database-mode retry delay. SQS gets its delay from the visibility timeout;
#: with no SQS the retried row must be parked itself, or the next receive picks
#: it up instantly and burns every attempt on one transient blip (README 13.5).
#: It grows with the attempt count and is capped so a job is never parked long.
RETRY_BACKOFF_BASE_SECONDS = 5.0
RETRY_BACKOFF_MAX_SECONDS = 60.0


@dataclass(frozen=True)
class ClaimedJob:
    """Everything the worker needs, read inside the claim transaction.

    Returning plain values rather than ORM instances keeps processing free of
    detached-instance and lazy-load surprises once the session has closed.
    """

    job_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    storage_key: str
    rules_snapshot: dict
    attempts: int
    #: The exact lease this claim was granted. It doubles as a fencing token:
    #: outcome writes only apply while the row still carries it, so a worker
    #: whose lease was reclaimed cannot drag a finished job backwards.
    lease_until: datetime


class JobWorker:
    def __init__(self, queue: JobQueue | None = None, notifier=None) -> None:
        self._queue = queue
        self._notifier = notifier
        self._stop = threading.Event()

    @property
    def queue(self) -> JobQueue:
        if self._queue is None:
            self._queue = get_queue()
        return self._queue

    @property
    def notifier(self):
        if self._notifier is None:
            self._notifier = get_notifier()
        return self._notifier

    # ---------------------------------------------------------------- loop

    def run_once(self, *, wait_seconds: int = SQS_WAIT_SECONDS, max_messages: int = 1) -> int:
        """Receive one batch and handle it. Returns how many messages it saw."""
        messages = self.queue.receive(wait_seconds=wait_seconds, max_messages=max_messages)
        for message in messages:
            try:
                acknowledge = self.handle(message)
            except Exception:
                # An unexpected crash leaves the message unacknowledged so the
                # visibility timeout redelivers it (README 13.5).
                logger.exception("Handling job %s failed.", message.job_id)
                acknowledge = False

            if not acknowledge:
                continue
            try:
                self.queue.delete(message)
            except Exception:
                # The work is already committed; a failed delete only means a
                # duplicate delivery later, which the claim logic absorbs.
                logger.warning("Could not acknowledge job %s.", message.job_id, exc_info=True)
        return len(messages)

    def run_forever(self) -> None:
        logger.info("Worker started (queue=%s).", type(self.queue).__name__)
        while not self._stop.is_set():
            try:
                handled = self.run_once()
            except Exception:
                logger.exception("Worker iteration failed; retrying.")
                self._stop.wait(ERROR_BACKOFF_SECONDS)
                continue

            if handled:
                # Drain a backlog without pausing: an available job is never
                # delayed by the idle poll interval.
                continue

            if isinstance(self.queue, DatabaseJobQueue):
                # Database mode has no long poll, so re-selecting immediately
                # would peg a CPU and hammer Postgres on an empty queue.
                # ``database_queue_poll_seconds`` is the intended idle cadence.
                # Event.wait, not sleep, so stop() during the wait returns at
                # once instead of holding shutdown for a whole interval.
                self._stop.wait(get_settings().database_queue_poll_seconds)
        logger.info("Worker stopped.")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------- handling

    def handle(self, message: QueueMessage) -> bool:
        """Process one message. Returns whether it may be acknowledged."""
        claim, outcome = self._claim(message.job_id)

        if outcome != "claimed":
            # Completed duplicates (and redelivered INPUT failures) are safe to
            # acknowledge. Everything else is left unacknowledged: a live lease
            # means another worker owns it and the message should come back
            # later, a terminal FAILED row must stay for the DLQ rather than be
            # acked (README 13.5), and an unknown job is left for the DLQ too.
            # In database mode leaving a terminal row unacknowledged cannot spin
            # the worker: ``delete`` is a no-op and receive never selects FAILED.
            return outcome == "already_finished"

        assert claim is not None
        return self._process(claim)

    def _claim(self, job_id: uuid.UUID) -> tuple[ClaimedJob | None, str]:
        settings = get_settings()
        now = datetime.now(timezone.utc)

        with session_scope() as session:
            # SELECT ... FOR UPDATE serialises two workers racing for one job.
            job = session.execute(
                select(Job).where(Job.id == job_id).with_for_update()
            ).scalar_one_or_none()

            if job is None:
                logger.error("Message references unknown job %s; leaving it for the DLQ.", job_id)
                return None, "unknown"

            if job.status == JobStatus.COMPLETED.value:
                logger.info("Job %s already completed; acknowledging duplicate.", job_id)
                return None, "already_finished"

            if job.status == JobStatus.FAILED.value:
                if job.failure_kind == FailureKind.INPUT.value:
                    # A permanent input fault is already recorded, and README
                    # 13.5 calls it safe to acknowledge.
                    logger.info("Job %s already failed as invalid input; acknowledging duplicate.", job_id)
                    return None, "already_finished"
                # RETRY_EXHAUSTED (or any other terminal failure) must stay
                # unacknowledged so SQS redrives it to the DLQ; acknowledging it
                # here is exactly what would defeat the redrive policy.
                logger.info(
                    "Job %s already failed terminally (%s); leaving it for the DLQ.",
                    job_id,
                    job.failure_kind,
                )
                return None, "terminal_failure"

            if job.status == JobStatus.RUNNING.value and job.lease_until and job.lease_until > now:
                # Another worker holds a live lease. Leaving the message alone
                # is what prevents concurrent processing of one job.
                logger.info("Job %s is leased until %s; leaving it for later.", job_id, job.lease_until)
                return None, "busy_duplicate"

            # Claimed: an expired RUNNING lease is reusable, which is how a job
            # recovers after the previous worker died mid-run.
            job.status = JobStatus.RUNNING.value
            job.attempts += 1
            lease_until = now + timedelta(seconds=settings.lease_seconds)
            job.lease_until = lease_until
            if job.started_at is None:
                job.started_at = now

            claim = ClaimedJob(
                job_id=job.id,
                owner_id=job.owner_id,
                title=job.title,
                storage_key=job.dataset.storage_key,
                rules_snapshot=job.rules_snapshot,
                attempts=job.attempts,
                lease_until=lease_until,
            )

        return claim, "claimed"

    def _process(self, claim: ClaimedJob) -> bool:
        """Run the audit. Returns whether the message may be acknowledged."""
        settings = get_settings()
        started = time.monotonic()

        try:
            data = get_storage().read_bytes(claim.storage_key)
            parsed = parse_csv(
                data,
                max_upload_bytes=settings.max_upload_bytes,
                max_rows=settings.max_csv_rows,
                max_columns=settings.max_csv_columns,
            )
            rules = RuleSet.from_json(claim.rules_snapshot)
            result = validate(parsed, rules, error_preview_limit=settings.error_preview_limit)
        except ValidationInputError as exc:
            # The file can never be audited. That is a terminal input fault, not
            # an infrastructure problem, and it is safe to acknowledge.
            logger.info("Job %s failed as invalid input: %s", claim.job_id, exc.code)
            self._fail(claim, code=exc.code, message=exc.message, failure_kind="INPUT")
            return True
        except (StorageError, OSError, FileNotFoundError) as exc:
            logger.warning("Job %s hit a storage error.", claim.job_id, exc_info=exc)
            return self._retry(claim, "STORAGE_UNAVAILABLE", "Storage was unavailable.")
        except Exception:
            # A bug here — a malformed rules snapshot, a library fault — is not
            # an input problem and must not escape unmanaged: that would leave
            # the job RUNNING with a lease and its attempt already counted, and
            # nothing would compare attempts to MAX_ATTEMPTS. Route it through
            # the same retry/exhaustion decision so it always terminates, using
            # RETRY_EXHAUSTED once the attempts run out (README 13.5) and
            # INTERNAL_ERROR as the code so a bug is still distinguishable from
            # a transient outage. A job is never left RUNNING by this path.
            logger.exception("Job %s hit an unexpected error while validating.", claim.job_id)
            return self._retry(claim, "INTERNAL_ERROR", "An unexpected error occurred.")

        duration = time.monotonic() - started
        try:
            summary, artifacts = self._write_artifacts(claim, rules, parsed, result, duration)
        except (StorageError, OSError) as exc:
            logger.warning("Job %s could not write its outputs.", claim.job_id, exc_info=exc)
            return self._retry(claim, "STORAGE_UNAVAILABLE", "Outputs could not be written.")
        except Exception:
            logger.exception("Job %s hit an unexpected error while writing outputs.", claim.job_id)
            return self._retry(claim, "INTERNAL_ERROR", "An unexpected error occurred.")

        self._complete(claim, summary, artifacts)
        logger.info(
            "Job %s completed: %s/%s rows valid (%s rejected).",
            claim.job_id,
            result.valid_rows,
            result.total_rows,
            result.rejected_rows,
        )
        return True

    def _write_artifacts(
        self,
        claim: ClaimedJob,
        rules: RuleSet,
        parsed: ParsedCsv,
        result: ValidationResult,
        duration: float,
    ) -> tuple[dict, dict]:
        """Write the four artifacts under deterministic keys.

        Deterministic keys matter: a retry overwrites the same objects instead of
        leaving a second, contradictory set behind (README 12.1).
        """
        storage = get_storage()
        payloads = {
            "valid": build_valid_csv(parsed, result),
            "rejected": build_rejected_csv(parsed, result),
            "report": build_report_json(
                build_report(
                    job_id=str(claim.job_id),
                    rules=rules.to_json(),
                    result=result,
                    processing_seconds=duration,
                )
            ),
        }

        artifacts: dict = {}
        for kind, payload in payloads.items():
            key = output_key(claim.owner_id, claim.job_id, kind)
            storage.save_bytes(key, payload)
            artifacts[kind] = {"key": key, "size_bytes": len(payload)}

        # Record the original too, so artifacts_json describes the full set.
        try:
            original_size = len(storage.read_bytes(claim.storage_key))
        except (StorageError, OSError, FileNotFoundError):
            original_size = 0
        artifacts["original"] = {"key": claim.storage_key, "size_bytes": original_size}

        summary = {
            "total_rows": result.total_rows,
            "valid_rows": result.valid_rows,
            "rejected_rows": result.rejected_rows,
            "valid_percentage": result.valid_percentage,
        }
        return summary, artifacts

    # ------------------------------------------------------------- outcomes

    def _fenced(self, claim: ClaimedJob) -> tuple:
        """WHERE criteria that only match the row this worker still owns.

        ``lease_until`` doubles as a fencing token: if another worker reclaimed
        the expired lease it overwrote this value, so a stale worker's outcome
        write matches no rows and is dropped instead of regressing a finished
        job (README 13.4). No new column or migration is needed.
        """
        return (
            Job.id == claim.job_id,
            Job.status == JobStatus.RUNNING.value,
            Job.lease_until == claim.lease_until,
        )

    def _complete(self, claim: ClaimedJob, summary: dict, artifacts: dict) -> None:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            result = session.execute(
                update(Job)
                .where(*self._fenced(claim))
                .values(
                    summary_json=summary,
                    artifacts_json=artifacts,
                    status=JobStatus.COMPLETED.value,
                    completed_at=now,
                    lease_until=None,
                    error_code=None,
                    error_message=None,
                    failure_kind=None,
                )
            )
            if not result.rowcount:
                # The row was reclaimed while we ran. Its new owner's outcome is
                # authoritative; writing ours would drag it backwards and, with
                # the message already gone, nothing would ever fix it.
                logger.info("Job %s was reclaimed before completion; dropping stale write.", claim.job_id)
                return

            # Queued in this same transaction so a crash before publishing is
            # retryable rather than lost (README 13.6).
            job = session.get(Job, claim.job_id)
            event_type = event_type_for(job, summary)
            if event_type:
                record_event(session, job, event_type)

    def _fail(self, claim: ClaimedJob, *, code: str, message: str, failure_kind: str) -> None:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            result = session.execute(
                update(Job)
                .where(*self._fenced(claim))
                .values(
                    status=JobStatus.FAILED.value,
                    error_code=code,
                    error_message=message,
                    failure_kind=failure_kind,
                    completed_at=now,
                    lease_until=None,
                )
            )
            if not result.rowcount:
                logger.info("Job %s was reclaimed before failing; dropping stale write.", claim.job_id)
                return
            job = session.get(Job, claim.job_id)
            record_event(session, job, NotificationEventType.JOB_FAILED.value)

    def _retry(self, claim: ClaimedJob, code: str, message: str) -> bool:
        """Return a job to QUEUED, or fail it once attempts are exhausted.

        Returns whether the message may be acknowledged: a retryable failure is
        left unacknowledged so the visibility timeout redelivers it, and an
        exhausted one is left for the DLQ deliberately — acknowledging it would
        defeat the redrive policy (README 13.5).
        """
        now = datetime.now(timezone.utc)
        exhausted = claim.attempts >= MAX_ATTEMPTS

        values: dict = {"error_code": code, "error_message": message}
        if exhausted:
            values.update(
                status=JobStatus.FAILED.value,
                failure_kind="RETRY_EXHAUSTED",
                completed_at=now,
                lease_until=None,
            )
        else:
            # Park the job on a not-before lease_until instead of clearing it.
            # Database mode skips QUEUED rows until that passes, which is the
            # local stand-in for the SQS visibility-timeout delay; SQS mode
            # ignores the value because its own visibility timeout governs
            # redelivery. Without this the next receive would pick the row up
            # instantly and all attempts would burn on one blip.
            values.update(
                status=JobStatus.QUEUED.value,
                lease_until=now + timedelta(seconds=_retry_backoff_seconds(claim.attempts)),
            )

        with session_scope() as session:
            result = session.execute(update(Job).where(*self._fenced(claim)).values(**values))
            if not result.rowcount:
                logger.info("Job %s was reclaimed before retry; dropping stale write.", claim.job_id)
                return False

            if exhausted:
                job = session.get(Job, claim.job_id)
                record_event(session, job, NotificationEventType.JOB_FAILED.value)
                logger.error("Job %s exhausted %s attempts.", claim.job_id, claim.attempts)
            else:
                logger.info("Job %s returned to QUEUED (attempt %s).", claim.job_id, claim.attempts)

        return False


def _retry_backoff_seconds(attempts: int) -> float:
    """Capped linear backoff before a retried job is eligible again.

    The first failure waits the base interval and each later one waits a further
    multiple, so a transient blip has time to clear while a job is never parked
    for long. SQS mode gets the same effect from its visibility timeout.
    """
    return min(RETRY_BACKOFF_BASE_SECONDS * max(attempts, 1), RETRY_BACKOFF_MAX_SECONDS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    worker = JobWorker()

    # Ctrl+C and SIGTERM stop the loop after the current job, so a half-finished
    # audit is never left holding a lease it cannot release.
    def _handle_signal(signum, _frame):
        logger.info("Received signal %s; finishing the current job.", signum)
        worker.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        pass  # Not available on every platform.

    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()
