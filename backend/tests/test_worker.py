"""Worker concurrency-fix regression tests (README 13.4, 13.5, 13.8).

These cover the worker's decision logic only. No database and no AWS are needed:
the session scope and storage are replaced with in-test doubles, so the fencing,
retry, acknowledgement and idle-poll decisions can be asserted directly.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import worker as worker_module
from app.models import FailureKind, Job, JobStatus, NotificationEventType
from app.services import dispatcher as dispatcher_module
from app.services.dispatcher import DatabaseJobQueue, QueueMessage
from app.services.rules import RuleSetError
from app.worker import (
    MAX_ATTEMPTS,
    RETRY_BACKOFF_BASE_SECONDS,
    RETRY_BACKOFF_MAX_SECONDS,
    ClaimedJob,
    JobWorker,
    _retry_backoff_seconds,
)

# --------------------------------------------------------------------- helpers


def _claim(attempts: int = 1) -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        title="Audit",
        storage_key="uploads/owner/dataset/original.csv",
        rules_snapshot={},
        attempts=attempts,
        lease_until=datetime.now(timezone.utc) + timedelta(seconds=120),
    )


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "lease_seconds": 120,
        "database_queue_poll_seconds": 3.0,
        "max_upload_bytes": 5 * 1024 * 1024,
        "max_csv_rows": 20_000,
        "max_csv_columns": 50,
        "error_preview_limit": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@contextmanager
def _yielding(session):
    """Stand in for ``session_scope`` when the test owns the session."""
    yield session


def _use_session(monkeypatch, module, session) -> None:
    monkeypatch.setattr(module, "session_scope", lambda: _yielding(session))


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _ScalarResult:
    def __init__(self, values) -> None:
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _Session:
    """Captures Core statements and returns a preset affected-row count.

    The preset ``rowcount`` makes the worker's "no rows affected means another
    worker owns the job" branch reachable without a database.
    """

    def __init__(self, *, rowcount: int = 1, job=None) -> None:
        self.rowcount = rowcount
        self.job = job
        self.executed: list = []

    def execute(self, statement):
        self.executed.append(statement)
        return _Result(self.rowcount)

    def get(self, model, pk):
        return self.job


class _ClaimSession:
    """A session whose locked SELECT returns one prebuilt job row."""

    def __init__(self, job) -> None:
        self.job = job

    def execute(self, statement):
        job = self.job
        return SimpleNamespace(scalar_one_or_none=lambda: job)


class _CaptureSession:
    """A session that keeps the statement it was handed and returns fixed rows."""

    def __init__(self, values=()) -> None:
        self.statement = None
        self._values = list(values)

    def execute(self, statement):
        self.statement = statement
        return _ScalarResult(self._values)


class _RecordingStop:
    """Records the timeouts passed to ``wait`` and stops after a set count.

    ``run_forever`` is an infinite loop, so ``set_after_waits`` makes it exit
    deterministically once the idle waits have been observed.
    """

    def __init__(self, set_after_waits: int = 1) -> None:
        self.timeouts: list[float | None] = []
        self._set_after = set_after_waits
        self._waits = 0
        self._stopped = False

    def is_set(self) -> bool:
        return self._stopped

    def set(self) -> None:
        self._stopped = True

    def wait(self, timeout: float | None = None) -> bool:
        self.timeouts.append(timeout)
        self._waits += 1
        if self._waits >= self._set_after:
            # Simulates stop() arriving while the worker is idle: an Event.wait
            # returns immediately, which is what keeps shutdown prompt.
            self._stopped = True
        return self._stopped


class _StaticStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read_bytes(self, key: str) -> bytes:
        return self.payload


def _raise(exc: Exception):
    raise exc


def _update_values(statement) -> dict:
    """Unwrap an UPDATE's ``column -> bound value`` into plain Python values."""
    return {
        column.name: getattr(value, "value", value)
        for column, value in statement._values.items()
    }


# ------------------------------------------------------------ D1: idle polling


def test_idle_worker_polls_at_the_configured_interval(monkeypatch):
    queue = DatabaseJobQueue()
    monkeypatch.setattr(queue, "receive", lambda **kwargs: [])
    monkeypatch.setattr(worker_module, "get_settings", lambda: _settings())
    worker = JobWorker(queue=queue)
    worker._stop = _RecordingStop(set_after_waits=2)

    worker.run_forever()

    # Two empty polls, each separated by the configured interval: no hot spin,
    # and the wait is interruptible so stop() ends it promptly.
    assert worker._stop.timeouts == [3.0, 3.0]


def test_backlog_is_drained_without_waiting(monkeypatch):
    queue = DatabaseJobQueue()
    pending = [QueueMessage(job_id=uuid.uuid4())]
    monkeypatch.setattr(queue, "receive", lambda **kwargs: [pending.pop()] if pending else [])
    monkeypatch.setattr(worker_module, "get_settings", lambda: _settings())
    worker = JobWorker(queue=queue)
    monkeypatch.setattr(worker, "handle", lambda message: True)
    worker._stop = _RecordingStop(set_after_waits=1)

    worker.run_forever()

    # Only the empty poll waits; the non-empty one loops straight back.
    assert worker._stop.timeouts == [3.0]


# ------------------------------------------- D2: database queue recalls expired leases


def test_database_receive_offers_expired_running_rows(monkeypatch):
    captured = _CaptureSession()
    _use_session(monkeypatch, dispatcher_module, captured)

    DatabaseJobQueue().receive(wait_seconds=0, max_messages=1)

    sql = str(captured.statement.compile(compile_kwargs={"literal_binds": True}))
    # A RUNNING row is offered again only when its lease has expired; without
    # this nothing could ever reclaim a job after its worker died (README 13.8).
    assert "jobs.status = 'RUNNING'" in sql
    assert "lease_until <= " in sql


def test_database_receive_still_offers_pending_and_queued(monkeypatch):
    captured = _CaptureSession()
    _use_session(monkeypatch, dispatcher_module, captured)

    DatabaseJobQueue().receive(wait_seconds=0, max_messages=1)

    sql = str(captured.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "jobs.status = 'PENDING_DISPATCH'" in sql
    assert "jobs.status = 'QUEUED'" in sql


def test_database_receive_maps_rows_to_messages(monkeypatch):
    job_id = uuid.uuid4()
    _use_session(monkeypatch, dispatcher_module, _CaptureSession([job_id]))

    messages = DatabaseJobQueue().receive(wait_seconds=0, max_messages=1)

    assert messages == [QueueMessage(job_id=job_id)]


# ------------------------------------------- D4: FAILED messages stay for the DLQ


@pytest.mark.parametrize(
    ("outcome", "expected_ack"),
    [
        ("already_finished", True),  # completed duplicate (or INPUT failure)
        ("terminal_failure", False),  # RETRY_EXHAUSTED: leave for the DLQ
        ("busy_duplicate", False),  # another worker holds a live lease
        ("unknown", False),  # unknown job: leave for the DLQ
    ],
)
def test_handle_acknowledges_only_safe_outcomes(monkeypatch, outcome, expected_ack):
    worker = JobWorker(queue=object())
    monkeypatch.setattr(worker, "_claim", lambda job_id: (None, outcome))

    assert worker.handle(QueueMessage(job_id=uuid.uuid4())) is expected_ack


def test_claim_leaves_retry_exhausted_failure_for_the_dlq(monkeypatch):
    job = Job(
        id=uuid.uuid4(),
        status=JobStatus.FAILED.value,
        failure_kind=FailureKind.RETRY_EXHAUSTED.value,
    )
    _use_session(monkeypatch, worker_module, _ClaimSession(job))
    monkeypatch.setattr(worker_module, "get_settings", lambda: _settings())

    claim, outcome = JobWorker(queue=object())._claim(job.id)

    assert claim is None
    assert outcome == "terminal_failure"


def test_claim_acknowledges_input_failure(monkeypatch):
    job = Job(
        id=uuid.uuid4(),
        status=JobStatus.FAILED.value,
        failure_kind=FailureKind.INPUT.value,
    )
    _use_session(monkeypatch, worker_module, _ClaimSession(job))
    monkeypatch.setattr(worker_module, "get_settings", lambda: _settings())

    claim, outcome = JobWorker(queue=object())._claim(job.id)

    assert claim is None
    assert outcome == "already_finished"


def test_claim_acknowledges_completed_duplicate(monkeypatch):
    job = Job(id=uuid.uuid4(), status=JobStatus.COMPLETED.value)
    _use_session(monkeypatch, worker_module, _ClaimSession(job))
    monkeypatch.setattr(worker_module, "get_settings", lambda: _settings())

    claim, outcome = JobWorker(queue=object())._claim(job.id)

    assert claim is None
    assert outcome == "already_finished"


def test_claim_leaves_live_lease_alone(monkeypatch):
    job = Job(
        id=uuid.uuid4(),
        status=JobStatus.RUNNING.value,
        lease_until=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    _use_session(monkeypatch, worker_module, _ClaimSession(job))
    monkeypatch.setattr(worker_module, "get_settings", lambda: _settings())

    claim, outcome = JobWorker(queue=object())._claim(job.id)

    assert claim is None
    assert outcome == "busy_duplicate"


# ------------------------------------- D3: unexpected errors still terminate


def test_unexpected_error_routes_through_retry_exhaustion(monkeypatch):
    worker = JobWorker(queue=object())
    claim = _claim(attempts=1)
    monkeypatch.setattr(worker_module, "get_settings", lambda: _settings())
    monkeypatch.setattr(worker_module, "get_storage", lambda: _StaticStorage(b"student_id,name\n1,A\n"))
    # RuleSetError subclasses ValueError, not ValidationInputError: it used to
    # escape _process and leave the job RUNNING forever.
    monkeypatch.setattr(
        worker_module.RuleSet,
        "from_json",
        lambda payload: _raise(RuleSetError("BAD_RULES", "bad snapshot")),
    )
    calls: list[tuple] = []
    monkeypatch.setattr(worker, "_retry", lambda c, code, message: calls.append((code, message)) or False)

    assert worker._process(claim) is False
    assert calls[0][0] == "INTERNAL_ERROR"


def test_invalid_input_fails_terminal_and_acknowledges(monkeypatch):
    worker = JobWorker(queue=object())
    claim = _claim(attempts=1)
    monkeypatch.setattr(worker_module, "get_settings", lambda: _settings())
    # An empty file is a ValidationInputError, a permanent input fault.
    monkeypatch.setattr(worker_module, "get_storage", lambda: _StaticStorage(b""))
    recorded: dict = {}
    monkeypatch.setattr(worker, "_fail", lambda c, **kwargs: recorded.update(kwargs))

    assert worker._process(claim) is True
    assert recorded["failure_kind"] == "INPUT"
    assert recorded["code"] == "EMPTY_DATASET"


# ------------------------------------------------------- D5: fenced outcome writes


def test_complete_drops_stale_write_when_reclaimed(monkeypatch):
    claim = _claim()
    session = _Session(rowcount=0)
    _use_session(monkeypatch, worker_module, session)
    events: list = []
    monkeypatch.setattr(worker_module, "record_event", lambda *args, **kwargs: events.append(args))
    worker = JobWorker(queue=object())

    worker._complete(claim, {"total_rows": 1, "rejected_rows": 0}, {})

    assert events == []  # no notification for a job another worker now owns
    where = str(session.executed[0].whereclause)
    assert "lease_until" in where and "status" in where  # fenced on the token


def test_complete_records_event_when_still_owner(monkeypatch):
    claim = _claim()
    job = Job(id=claim.job_id, owner_id=claim.owner_id, status=JobStatus.COMPLETED.value)
    session = _Session(rowcount=1, job=job)
    _use_session(monkeypatch, worker_module, session)
    events: list = []
    monkeypatch.setattr(
        worker_module,
        "record_event",
        lambda session, job, event_type: events.append(event_type),
    )
    worker = JobWorker(queue=object())

    worker._complete(claim, {"total_rows": 3, "rejected_rows": 2}, {})

    assert events == [NotificationEventType.JOB_COMPLETED_WITH_REJECTS.value]


def test_retry_drops_stale_write_when_reclaimed(monkeypatch):
    claim = _claim(attempts=1)
    session = _Session(rowcount=0)
    _use_session(monkeypatch, worker_module, session)
    worker = JobWorker(queue=object())

    assert worker._retry(claim, "STORAGE_UNAVAILABLE", "nope") is False
    where = str(session.executed[0].whereclause)
    assert "lease_until" in where and "status" in where


# --------------------------------------------------- D6: retry backoff in database mode


def test_retry_parks_the_job_behind_a_backoff(monkeypatch):
    claim = _claim(attempts=1)
    session = _Session(rowcount=1)
    _use_session(monkeypatch, worker_module, session)
    worker = JobWorker(queue=object())

    assert worker._retry(claim, "STORAGE_UNAVAILABLE", "transient") is False

    values = _update_values(session.executed[0])
    assert values["status"] == JobStatus.QUEUED.value
    # A not-before lease parks the row so the next receive skips it for a while.
    assert values["lease_until"] > datetime.now(timezone.utc)


def test_retry_exhaustion_fails_terminal(monkeypatch):
    claim = _claim(attempts=MAX_ATTEMPTS)
    session = _Session(rowcount=1, job=Job(id=claim.job_id, status=JobStatus.FAILED.value))
    _use_session(monkeypatch, worker_module, session)
    events: list = []
    monkeypatch.setattr(worker_module, "record_event", lambda s, j, t: events.append(t))
    worker = JobWorker(queue=object())

    assert worker._retry(claim, "INTERNAL_ERROR", "boom") is False

    values = _update_values(session.executed[0])
    assert values["status"] == JobStatus.FAILED.value
    assert values["failure_kind"] == "RETRY_EXHAUSTED"
    assert values["lease_until"] is None
    assert events == [NotificationEventType.JOB_FAILED.value]


def test_retry_backoff_grows_and_is_capped():
    assert _retry_backoff_seconds(1) == RETRY_BACKOFF_BASE_SECONDS
    assert _retry_backoff_seconds(2) == RETRY_BACKOFF_BASE_SECONDS * 2
    assert _retry_backoff_seconds(10_000) == RETRY_BACKOFF_MAX_SECONDS
