"""Exercise the complete deployed application through its public HTTPS URL.

Creates synthetic accounts and jobs, never prints passwords, tokens or signed
download URLs. The small fixtures remain as evidence in job history and S3.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import secrets
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]
RULES = {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16, "max": 100, "integer": True}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]},
}


def expect(condition, message):
    if not condition:
        raise RuntimeError(message)
    print("PASS " + message, flush=True)


def require(response, code, label):
    if response.status_code != code:
        # Do not include response bodies: an unexpected auth response could
        # contain a token. Status and operation are sufficient for diagnosis.
        raise RuntimeError(f"{label}: expected HTTP {code}, received {response.status_code}")
    return response.json()


def wait_job(client, job_id, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = require(client.get(f"/api/jobs/{job_id}"), 200, "Job status")
        if job["status"] == "COMPLETED":
            return job
        if job["status"] == "FAILED":
            raise RuntimeError(f"Job {job_id} failed: {job.get('error', {}).get('code')}")
        time.sleep(2)
    raise TimeoutError(f"Job {job_id} did not finish in {seconds} seconds")


def account(client, account_factory=None):
    if account_factory:
        email, password = account_factory()
    else:
        email = f"deployment-{secrets.token_hex(8)}@example.com"
        password = secrets.token_urlsafe(24)
        registered = require(client.post("/api/auth/register", json={"email": email, "password": password}), 201, "Registration")
        if registered.get("email_verification_required"):
            raise RuntimeError("Email verification is enabled. Run the deployer with controlled verified fixtures, or verify a real account interactively.")
    token = require(client.post("/api/auth/login", json={"email": email, "password": password}), 200, "Login")["access_token"]
    return token


def verify(base_url, controls=None, account_factory=None):
    if not base_url.startswith("https://"):
        raise ValueError("Deployment verification requires public HTTPS.")
    result = {"website": base_url, "jobs": [], "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with httpx.Client(base_url=base_url, timeout=40, follow_redirects=True) as client:
        for _ in range(60):
            try:
                ready = client.get("/api/ready")
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(5)
        else:
            raise RuntimeError("Public HTTPS readiness did not pass within five minutes.")
        expect(client.get("/").status_code == 200, "Frontend loads over HTTPS")
        expect(require(client.get("/deployment-mode.json"), 200, "Frontend manifest")["mode"] == "api", "Frontend uses the live API")
        require(ready, 200, "Database readiness")
        token = account(client, account_factory)
        client.headers["Authorization"] = f"Bearer {token}"
        expect(bool(require(client.get("/api/auth/me"), 200, "Current account")["id"]), "Verified-account login works" if account_factory else "Registration and login work")
        rule = require(client.post("/api/rule-sets", json={"name": "Deployment student records", "rules": RULES}), 201, "Create rule set")
        for filename, expected in [("students_dirty.csv", (6, 2, 4)), ("students_good.csv", (6, 6, 0))]:
            source = (ROOT / "samples" / filename).read_bytes()
            pause = controls is not None and filename == "students_dirty.csv"
            if pause:
                controls("stop-worker")
            try:
                accepted = require(client.post("/api/jobs", data={"title": f"AWS verification: {filename}", "rule_set_id": rule["id"]},
                    files={"file": (filename, source, "text/csv")}), 202, "CSV upload")
                if pause:
                    for _ in range(15):
                        waiting = require(client.get(f"/api/jobs/{accepted['job_id']}"), 200, "Paused worker job")
                        if waiting["status"] == "QUEUED":
                            break
                        time.sleep(1)
                    expect(waiting["status"] == "QUEUED", "API dispatches to SQS while the worker is stopped")
            finally:
                if pause:
                    controls("start-worker")
            job_id = accepted["job_id"]
            job = wait_job(client, job_id)
            summary = job["summary"]
            expect(tuple(summary[k] for k in ("total_rows", "valid_rows", "rejected_rows")) == expected,
                   f"{filename}: correct row counts {expected}")
            report = require(client.get(f"/api/jobs/{job_id}/report"), 200, "Report endpoint")
            expect(report["summary"] == summary, f"{filename}: report matches job summary")
            for kind in ("original", "valid", "rejected", "report"):
                link = require(client.get(f"/api/jobs/{job_id}/downloads/{kind}"), 200, "Download authorization")
                # Use an unauthenticated client for S3; never forward the API's
                # bearer token to a different origin, and never log signed URLs.
                with httpx.Client(timeout=40, follow_redirects=True) as downloads:
                    response = downloads.get(link["url"])
                expect(response.status_code == 200, f"{filename}: {kind} S3 download works")
                if kind == "original":
                    expect(response.content == source, f"{filename}: original bytes preserved")
                elif kind == "report":
                    expect(response.json()["summary"] == summary, f"{filename}: downloaded report matches")
                else:
                    rows = list(csv.DictReader(io.StringIO(response.text)))
                    expect(len(rows) == expected[1 if kind == "valid" else 2], f"{filename}: {kind} export row count")
            result["jobs"].append({"id": job_id, "fixture": filename, "summary": summary})
        other_token = account(client, account_factory)
        with httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {other_token}"}, timeout=30) as other:
            job_id = result["jobs"][0]["id"]
            for endpoint in (f"/api/jobs/{job_id}", f"/api/jobs/{job_id}/report", f"/api/jobs/{job_id}/downloads/original"):
                expect(other.get(endpoint).status_code == 404, "Another account cannot access an owner's job/report/download")
        listed = require(client.get("/api/jobs"), 200, "Job history")
        expect(listed["total"] == 2, "Both completed jobs persist in owner history")
        if controls:
            controls("restart-services")
            for _ in range(30):
                try:
                    if client.get("/api/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(2)
            for saved in result["jobs"]:
                persisted = require(client.get(f"/api/jobs/{saved['id']}"), 200, "Persisted job")
                expect(persisted["summary"] == saved["summary"], "Job results survive API and worker restart")
            controls("check-notifications")
            controls("check-tls-renewal")
            result["worker_pause_recovery"] = "passed"
            result["service_restart_persistence"] = "passed"
            result["sns_delivery"] = "passed"
            result["certificate_renewal_dry_run"] = "passed"
    result["status"] = "passed"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verification = verify(args.base_url)
    if args.output:
        args.output.write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
