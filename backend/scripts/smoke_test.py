"""End-to-end smoke test for the local stack (README 13.8).

Proves the API, the publisher and the worker agree end-to-end without any AWS
involvement: log in, upload the dirty fixture, wait for the job to finish, then
check the counts, the report and a signed download.

Requires the API on 127.0.0.1:8000 and a running worker, plus a seeded account::

    python -m app.seed --email demo@example.com --password demo-password-123
    python scripts/smoke_test.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = REPO_ROOT / "samples"

#: README 11.3's documented outcome for the dirty fixture.
EXPECTED_DIRTY = {"total_rows": 6, "valid_rows": 2, "rejected_rows": 4, "valid_percentage": 33.33}
EXPECTED_GOOD = {"total_rows": 6, "valid_rows": 6, "rejected_rows": 0, "valid_percentage": 100.0}

TERMINAL = {"COMPLETED", "FAILED"}


def _api(base: str, token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=base, headers=headers, timeout=30.0)


def check(label: str, actual, expected) -> bool:
    ok = actual == expected
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}: {actual!r}" + ("" if ok else f" (expected {expected!r})"))
    return ok


def run(base: str, email: str, password: str) -> int:
    failures = 0

    print(f"1. Logging in as {email}")
    with _api(base) as client:
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        if response.status_code != 200:
            print(f"     login failed: {response.status_code} {response.text}")
            return 1
        token = response.json()["access_token"]
    print("     ok")

    with _api(base, token) as client:
        print("2. Finding the seeded rule set")
        rule_sets = client.get("/api/rule-sets").json()
        if not rule_sets:
            print("     no rule sets; run app.seed first")
            return 1
        rule_set_id = rule_sets[0]["id"]
        print(f"     using '{rule_sets[0]['name']}' ({rule_set_id})")

        for name, expected in (("students_dirty.csv", EXPECTED_DIRTY), ("students_good.csv", EXPECTED_GOOD)):
            print(f"\n3. Uploading samples/{name}")
            with open(SAMPLES / name, "rb") as handle:
                response = client.post(
                    "/api/jobs",
                    data={"title": f"Smoke test: {name}", "rule_set_id": rule_set_id},
                    files={"file": (name, handle, "text/csv")},
                )
            if response.status_code != 202:
                print(f"     upload failed: {response.status_code} {response.text}")
                failures += 1
                continue

            job_id = response.json()["job_id"]
            print(f"     accepted 202, job_id={job_id}, status={response.json()['status']}")

            print("4. Polling for a terminal status")
            job = {}
            for _ in range(30):
                job = client.get(f"/api/jobs/{job_id}").json()
                if job["status"] in TERMINAL:
                    break
                time.sleep(1.0)

            if job.get("status") != "COMPLETED":
                print(f"     did not complete: {json.dumps(job, indent=2)}")
                failures += 1
                continue
            print(f"     COMPLETED after {job.get('completed_at')}")

            print("5. Checking counts against README 11.3")
            for key, value in expected.items():
                failures += not check(key, job["summary"][key], value)

            print("6. Checking the report and the job against the frontend contract")
            report = client.get(f"/api/jobs/{job_id}/report")
            failures += not check("report status", report.status_code, 200)
            if report.status_code == 200:
                body = report.json()
                failures += not check("report schema_version", body["schema_version"], "1.0")
                failures += not check(
                    "report summary.total_rows", body["summary"]["total_rows"], expected["total_rows"]
                )
                failures += _check_frontend_report(body)

            failures += _check_frontend_job(job, original_name=name, rule_set_id=rule_set_id)

            failures += not check(
                "available_downloads",
                sorted(job["available_downloads"]),
                ["original", "rejected", "report", "valid"],
            )

            print("7. Checking a signed download")
            link = client.get(f"/api/jobs/{job_id}/downloads/rejected")
            failures += not check("download url status", link.status_code, 200)
            if link.status_code == 200:
                # The signed URL is fetched without an Authorization header,
                # exactly as the browser would via window.location.assign.
                with httpx.Client(base_url=base, timeout=30.0) as anon:
                    fetched = anon.get(link.json()["url"])
                failures += not check("signed download status", fetched.status_code, 200)
                failures += not check(
                    "signed download is CSV", fetched.headers.get("content-type", "").split(";")[0], "text/csv"
                )

    print("\n" + ("All checks passed." if not failures else f"{failures} check(s) FAILED."))
    return 1 if failures else 0


def _check_frontend_report(body: dict) -> int:
    """Exercise the exact expressions ``JobDetails.jsx`` runs over the report.

    The dashboard renders straight off these keys, so a missing or wrongly-typed
    field shows up as a blank panel or a TypeError in the browser rather than as
    an HTTP error here. Mirroring the reads means a contract break fails the
    smoke test instead of the demo.

    ``docs/frontend-api-expectations.md`` is the frontend's statement of this
    shape; this function is the backend's check that it still holds.
    """
    failures = 0

    for key in (
        "schema_version",
        "job_id",
        "processed_at",
        "rules_snapshot",
        "summary",
        "failure_counts",
        "dataset_errors",
        "error_preview",
        "processing_duration_ms",
    ):
        failures += not check(f"report.{key} present", key in body, True)

    # `report.dataset_errors?.length > 0` then `.map(e => e.message)`
    for error in body.get("dataset_errors") or []:
        failures += not check(
            f"dataset_errors message ({error.get('code')})", bool(error.get("message")), True
        )

    # `report.failure_counts.map(...)`. A dict here has no `.length`, so the UI
    # silently falls back to "All checks passed" on a file full of failures.
    counts = body.get("failure_counts")
    failures += not check("failure_counts is a list", isinstance(counts, list), True)
    for entry in counts if isinstance(counts, list) else []:
        failures += not check(
            f"failure_counts entry ({entry.get('code')}/{entry.get('column')})",
            sorted(entry) == ["code", "column", "count", "message"],
            True,
        )
        failures += not check(
            f"failure_counts count is numeric ({entry.get('code')})",
            isinstance(entry.get("count"), int),
            True,
        )

    # `Object.entries(row.values).map(...)` raises on undefined.
    for row in body.get("error_preview") or []:
        number = row.get("record_number")
        failures += not check(
            f"error_preview[{number}].values is a non-empty map",
            isinstance(row.get("values"), dict) and bool(row["values"]),
            True,
        )
        failures += not check(f"error_preview[{number}].errors", bool(row.get("errors")), True)

    # `<RuleSummary rules={report.rules_snapshot} />`
    snapshot = body.get("rules_snapshot") or {}
    for key in (
        "required_columns",
        "required_values",
        "unique_columns",
        "numeric_ranges",
        "allowed_values",
    ):
        failures += not check(f"rules_snapshot.{key}", key in snapshot, True)

    return failures


def _check_frontend_job(job: dict, *, original_name: str, rule_set_id: str) -> int:
    """The job fields the dashboard shows beside a dataset."""
    failures = 0

    failures += not check("job.original_name", job.get("original_name"), original_name)
    failures += not check("job.rule_set_name is non-empty", bool(job.get("rule_set_name")), True)
    failures += not check("job.rule_set_id", job.get("rule_set_id"), rule_set_id)

    # The UI calls `job.summary.valid_percentage.toFixed(1)`, which a string
    # would survive but render wrongly ("33.33" has no toFixed).
    percentage = (job.get("summary") or {}).get("valid_percentage")
    failures += not check(
        "summary.valid_percentage is a number",
        isinstance(percentage, (int, float)) and not isinstance(percentage, bool),
        True,
    )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="demo-password-123")
    args = parser.parse_args()
    sys.exit(run(args.base_url, args.email, args.password))


if __name__ == "__main__":
    main()
