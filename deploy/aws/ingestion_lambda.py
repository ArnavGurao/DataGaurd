"""S3 -> Lambda -> authenticated ingestion API -> durable existing job publisher.

S3 invokes asynchronously. Failures retry and eventually reach the ingestion DLQ.
No CSV contents, credentials, signatures or filenames are logged.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

import boto3


def handler(event, context):
    if event.get("Event") == "s3:TestEvent":
        return {"test": True}
    ssm = boto3.client("ssm")
    prefix = os.environ["PARAMETER_PREFIX"]
    secret = ssm.get_parameter(Name=prefix + "/ingestion_secret", WithDecryption=True)["Parameter"]["Value"]
    api_url = ssm.get_parameter(Name=prefix + "/public_url")["Parameter"]["Value"]
    if not api_url.startswith("https://"):
        raise ValueError("HTTPS endpoint required")
    results = []
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3" or not record.get("eventName", "").startswith("ObjectCreated:"):
            continue
        source = record["s3"]
        key = urllib.parse.unquote_plus(source["object"]["key"])
        if not key.startswith("incoming/") or not key.lower().endswith(".csv"):
            continue
        version = source["object"].get("versionId")
        if not version or version == "null":
            raise ValueError("Ingestion requires versioned S3 objects")
        body = json.dumps({"bucket": source["bucket"]["name"], "key": key, "version_id": version}).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(api_url.rstrip("/") + "/api/ingestion/events", data=body,
            headers={"Content-Type": "application/json", "X-DataGuard-Timestamp": timestamp, "X-DataGuard-Signature": signature})
        with urllib.request.urlopen(req, timeout=40) as response:
            result = json.loads(response.read(8192))
        results.append(result)
        print(json.dumps({"job_id": result.get("job_id"), "duplicate": result.get("duplicate"), "ignored": result.get("ignored")}))
    return {"processed": len(results)}
