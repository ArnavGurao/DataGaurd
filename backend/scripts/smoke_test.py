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

            print("6. Checking the report and a download")
            report = client.get(f"/api/jobs/{job_id}/report")
            failures += not check("report status", report.status_code, 200)
            if report.status_code == 200:
                body = report.json()
                failures += not check("report schema_version", body["schema_version"], "1.0")
                failures += not check("report total_rows", body["counts"]["total_rows"], expected["total_rows"])

            failures += not check(
                "available_downloads",
                sorted(job["available_downloads"]),
                ["original", "rejected", "report", "valid"],
            )

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="demo-password-123")
    args = parser.parse_args()
    sys.exit(run(args.base_url, args.email, args.password))


if __name__ == "__main__":
    main()
