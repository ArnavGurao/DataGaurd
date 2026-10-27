import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

path = Path(__file__).resolve().parents[2] / "deploy/aws/ingestion_lambda.py"
spec = importlib.util.spec_from_file_location("ingestion_lambda", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_lambda_decodes_key_and_signs_exact_version(monkeypatch):
    monkeypatch.setenv("PARAMETER_PREFIX", "/dataguard/test")
    monkeypatch.setattr(module.boto3, "client", lambda *a: SimpleNamespace(get_parameter=lambda **kw: {
        "Parameter": {"Value": "secret" if kw["Name"].endswith("ingestion_secret") else "https://example.test"}}))
    requests = []
    def urlopen(req, **kw):
        requests.append(req)
        return io.BytesIO(b'{"job_id":"test","duplicate":false}')
    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    record = {"eventSource": "aws:s3", "eventName": "ObjectCreated:Put", "s3": {
        "bucket": {"name": "bucket"}, "object": {"key": "incoming/id/student+data.csv", "versionId": "v1"}}}
    assert module.handler({"Records": [record]}, None) == {"processed": 1}
    req = requests[0]
    assert json.loads(req.data)["key"] == "incoming/id/student data.csv"
    assert json.loads(req.data)["version_id"] == "v1"
    timestamp = req.get_header("X-dataguard-timestamp")
    assert req.get_header("X-dataguard-signature") == hmac.new(b"secret", timestamp.encode() + b"." + req.data, hashlib.sha256).hexdigest()


def test_s3_validation_event_requires_no_credentials():
    assert module.handler({"Event": "s3:TestEvent"}, None) == {"test": True}


def test_api_failure_is_retried_by_lambda(monkeypatch):
    monkeypatch.setenv("PARAMETER_PREFIX", "/dataguard/test")
    monkeypatch.setattr(module.boto3, "client", lambda *a: SimpleNamespace(get_parameter=lambda **kw: {
        "Parameter": {"Value": "secret" if kw["Name"].endswith("ingestion_secret") else "https://example.test"}}))
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        module.handler({"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put", "s3": {
            "bucket": {"name": "bucket"}, "object": {"key": "incoming/id/a.csv", "versionId": "v1"}}}]}, None)
