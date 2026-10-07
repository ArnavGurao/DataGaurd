"""Crash-recovery check for the database queue (README 13.8).

README 13.8 requires: *restart the worker during a job, wait for its lease to
expire, and verify it can finish.* Killing a worker at exactly the right moment
is not reproducible from a script, so this reproduces the same state
deterministically instead: it uploads a job and then forces its row into the
state a crashed worker leaves behind -- ``RUNNING`` with an expired lease and a
half-finished attempt. A worker that re-delivers expired leases picks it up and
finishes it; a worker that only looks at ``PENDING_DISPATCH``/``QUEUED`` leaves
it ``RUNNING`` forever, and the job is silently lost.

The upload is deliberately forced *after* the fact, so the check is race-free:
if the running worker finished the job first, the forced update puts the row
back into the crashed state anyway, and it still has to be recovered.

Requires the API on 127.0.0.1:8000, a running worker, and a seeded account:
    python -m app.seed --email demo@example.com --password demo-password-123
    python scripts/check_crash_recovery.py

Exit code 0 means the job was recovered; 1 means it was stranded.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    # Running this file directly puts scripts/ on sys.path, not backend/, so
    # `app` would not import without this.
    sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
from sqlalchemy import update  # noqa: E402

from app.database import session_scope  # noqa: E402
from app.models import Job, JobStatus  # noqa: E402

TERMINAL = {"COMPLETED", "FAILED"}

#: How long to wait for the recovered job to finish. Generous: the job only has
#: to survive one poll interval plus one validation run.
RECOVERY_TIMEOUT_SECONDS = 45


def crash_the_job(job_id: str) -> None:
    """Leave the row exactly as a worker killed mid-job would leave it."""
    with session_scope() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.RUNNING.value,
                attempts=1,
                # Expired: this is the lease the dead worker never renewed.
                lease_until=datetime.now(timezone.utc) - timedelta(minutes=5),
                started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                completed_at=None,
                summary_json=None,
                artifacts_json=None,
                error_code=None,
                error_message=None,
                failure_kind=None,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="demo-password-123")
    parser.add_argument("--fixture", default="students_good.csv")
    args = parser.parse_args()

    fixture = Path(__file__).resolve().parents[2] / "samples" / args.fixture
    if not fixture.exists():
        print(f"missing fixture: {fixture}")
        sys.exit(1)

    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        login = client.post(
            "/api/auth/login", json={"email": args.email, "password": args.password}
        )
        if login.status_code != 200:
            print(f"login failed: {login.status_code} {login.text}")
            sys.exit(1)
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

        rule_sets = client.get("/api/rule-sets").json()
        if not rule_sets:
            print("no rule sets; run app.seed first")
            sys.exit(1)

        with open(fixture, "rb") as handle:
            upload = client.post(
                "/api/jobs",
                data={"title": "Crash recovery check", "rule_set_id": rule_sets[0]["id"]},
                files={"file": (fixture.name, handle, "text/csv")},
            )
        if upload.status_code != 202:
            print(f"upload failed: {upload.status_code} {upload.text}")
            sys.exit(1)

        job_id = upload.json()["job_id"]
        print(f"1. Uploaded {fixture.name} as job {job_id}")

        crash_the_job(job_id)
        print("2. Forced the row to RUNNING with an expired lease (simulates a crash)")
        print("   and left it for the worker to recover.")

        print(f"3. Waiting up to {RECOVERY_TIMEOUT_SECONDS}s for it to finish")
        deadline = time.monotonic() + RECOVERY_TIMEOUT_SECONDS
        job = {}
        while time.monotonic() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in TERMINAL:
                break
            time.sleep(1.0)

    status = job.get("status")
    if status == "COMPLETED":
        summary = job.get("summary") or {}
        print(f"   recovered: COMPLETED with {summary.get('valid_rows')} valid rows")
        print("\nPASS - an expired lease is re-delivered and the job finishes.")
        return

    print(f"   still {status!r} after {RECOVERY_TIMEOUT_SECONDS}s")
    print(
        "\nFAIL - the job was never re-delivered. The queue is selecting only\n"
        "PENDING_DISPATCH/QUEUED, so a row left RUNNING by a crashed worker can\n"
        "never be claimed again. README 13.8 requires this to recover."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
