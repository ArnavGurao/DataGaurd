"""Cloud acceptance checks with synthetic fixtures; never logs credentials."""
import json
import secrets
import time
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from verify import account, expect, require, wait_job, RULES


def verify_features(base_url, session, outputs, account_factory):
    result = {}
    s3 = session.client("s3")
    with httpx.Client(base_url=base_url, timeout=60) as client:
        token = account(client, account_factory)
        client.headers["Authorization"] = "Bearer " + token
        rule = require(client.post("/api/rule-sets", json={"name": "Automatic ingestion acceptance", "rules": RULES}), 201, "Ingestion rule")
        source = require(client.post("/api/ingestion/sources", json={"name": "Cloud acceptance", "rule_set_id": rule["id"]}), 201, "Ingestion folder")
        expect(client.post("/api/ingestion/events", json={}).status_code == 401, "Unsigned ingestion callbacks are rejected")
        key = source["prefix"] + "student data.csv"
        root = Path(__file__).resolve().parents[2]
        version = None
        for fixture, expected, count in [("students_dirty.csv", 4, 1), ("students_good.csv", 0, 2)]:
            response = s3.put_object(Bucket=outputs["DataBucket"], Key=key, Body=(root / "samples" / fixture).read_bytes())
            version = response["VersionId"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                history = require(client.get("/api/jobs"), 200, "Ingestion history")
                if history["total"] == count:
                    break
                time.sleep(3)
            else:
                raise RuntimeError("S3 upload did not create a job through Lambda; inspect ingestion logs/DLQ.")
            job = wait_job(client, history["items"][0]["job_id"])
            expect(job["summary"]["rejected_rows"] == expected, f"S3 version {count} creates and validates the correct job")
        record = {"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put", "s3": {
            "bucket": {"name": outputs["DataBucket"]}, "object": {"key": quote_plus(key), "versionId": version}}}]}
        response = session.client("lambda").invoke(FunctionName=outputs["IngestionFunction"], Payload=json.dumps(record).encode())
        response["Payload"].read()
        expect(not response.get("FunctionError"), "Lambda handles a duplicate notification")
        expect(require(client.get("/api/jobs"), 200, "Deduplicated history")["total"] == 2, "Duplicate S3 notification does not create another job")
        require(client.delete(f"/api/ingestion/sources/{source['id']}"), 200, "Disable ingestion folder")
        expect(client.post("/api/rule-sets/draft", json={}).status_code == 404, "English rule generation endpoint is removed")
        result["s3_lambda_ingestion"] = "passed"
        result["s3_version_isolation_and_deduplication"] = "passed"

        email = f"unverified-{secrets.token_hex(8)}@example.com"
        password = secrets.token_urlsafe(24)
        registered = require(client.post("/api/auth/register", json={"email": email, "password": password}), 201, "Unverified registration")
        expect(registered["email_verification_required"], "Registration requires email verification")
        expect(client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 403, "Unverified accounts cannot log in")
        expect(client.post("/api/auth/verify-email", json={"email": email, "code": "000000"}).status_code == 422, "Invalid email verification code is rejected")
        result["email_verification_gate"] = "passed"
        result["email_inbox_acceptance"] = "pending real inbox code confirmation"
    return result
