"""Exercise auth gates, single-use codes, ingestion idempotency."""
import hashlib
import hmac
import io
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth, config
from app.database import Base, get_db
from app.main import app
from app.models import IngestionSource, Job, RuleSet, User
from app.routers import auth as auth_routes, ingestion
from app.services import verification


@compiles(JSONB, "sqlite")
def sqlite_json(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def feature_env(monkeypatch):
    settings = config.Settings(_env_file=None, app_env="local", db_password="test-password",
        jwt_secret="test-secret-at-least-32-characters-long", email_verification_required=True,
        storage_mode="s3", s3_bucket_name="test-data", ingestion_secret="test-ingestion-secret")
    for module in (auth, auth_routes, ingestion, verification):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def dependency():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    @contextmanager
    def scope():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_db] = dependency
    monkeypatch.setattr(ingestion, "session_scope", scope)
    codes = []
    monkeypatch.setattr(verification, "deliver_code", lambda email, code: codes.append((email, code)))
    client = TestClient(app)
    yield SimpleNamespace(client=client, engine=engine, settings=settings, codes=codes, scope=scope)
    app.dependency_overrides.clear()
    client.close()
    engine.dispose()


def register(env, email="test@example.com"):
    response = env.client.post("/api/auth/register", json={"email": email, "password": "test-password-123"})
    assert response.status_code == 201
    return response.json()


def verified_login(env, email="test@example.com"):
    result = register(env, email)
    code = next(code for target, code in reversed(env.codes) if target == email)
    assert env.client.post("/api/auth/verify-email", json={"email": email, "code": code}).status_code == 200
    token = env.client.post("/api/auth/login", json={"email": email, "password": "test-password-123"}).json()["access_token"]
    return result["id"], {"Authorization": "Bearer " + token}


def test_email_required_single_use_and_existing_tokens_blocked(feature_env):
    env = feature_env
    user = register(env)
    assert user["email_verification_required"] is True
    body = {"email": user["email"], "password": "test-password-123"}
    assert env.client.post("/api/auth/login", json=body).status_code == 403
    old_token = auth.create_access_token(uuid.UUID(user["id"]))
    assert env.client.get("/api/jobs", headers={"Authorization": "Bearer " + old_token}).status_code == 403
    code = env.codes[-1][1]
    with env.scope() as db:
        stored = db.get(User, uuid.UUID(user["id"]))
        assert stored.verification_digest != code and len(stored.verification_digest) == 64
    payload = {"email": user["email"], "code": code}
    assert env.client.post("/api/auth/verify-email", json=payload).status_code == 200
    assert env.client.post("/api/auth/verify-email", json=payload).status_code == 422
    assert env.client.post("/api/auth/login", json=body).status_code == 200


def test_code_lockout_expiry_and_resend_cooldown(feature_env):
    env = feature_env
    user = register(env)
    code = env.codes[-1][1]
    bad = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert env.client.post("/api/auth/verify-email", json={"email": user["email"], "code": bad}).status_code == 422
    assert env.client.post("/api/auth/verify-email", json={"email": user["email"], "code": code}).status_code == 422
    env.client.post("/api/auth/resend-verification", json={"email": user["email"]})
    assert len(env.codes) == 1
    with env.scope() as db:
        db.get(User, uuid.UUID(user["id"])).verification_sent_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    env.client.post("/api/auth/resend-verification", json={"email": user["email"]})
    assert len(env.codes) == 2
    with env.scope() as db:
        db.get(User, uuid.UUID(user["id"])).verification_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert env.client.post("/api/auth/verify-email", json={"email": user["email"], "code": env.codes[-1][1]}).status_code == 422


def test_failed_email_delivery_does_not_verify_account(feature_env, monkeypatch):
    monkeypatch.setattr(verification, "deliver_code", lambda *a: (_ for _ in ()).throw(RuntimeError("SES denied")))
    user = register(feature_env)
    with feature_env.scope() as db:
        stored = db.get(User, uuid.UUID(user["id"]))
        assert stored.email_verified_at is None and stored.verification_digest is None
    assert feature_env.client.post("/api/auth/login", json={"email": user["email"], "password": "test-password-123"}).status_code == 403


def test_ingestion_signature_and_replay_window():
    body, secret, timestamp = b"{}", "secret", "1000"
    signature = hmac.new(secret.encode(), b"1000." + body, hashlib.sha256).hexdigest()
    ingestion.verify_signature(body, timestamp, signature, secret, now=1001)
    for supplied, now in [(b"changed", 1001), (body, 1400)]:
        with pytest.raises(Exception):
            ingestion.verify_signature(supplied, timestamp, signature, secret, now=now)


def test_s3_version_idempotency_folder_ownership_and_revocation(feature_env, monkeypatch):
    env = feature_env
    owner, headers = verified_login(env)
    rule = env.client.post("/api/rule-sets", headers=headers, json={"name": "IDs", "rules": {"required_columns": ["id"]}}).json()
    response = env.client.post("/api/ingestion/sources", headers=headers, json={"name": "Imports", "rule_set_id": rule["id"]})
    assert response.status_code == 201
    source = response.json()
    _, other = verified_login(env, "other@example.com")
    assert env.client.post("/api/ingestion/sources", headers=other, json={"name": "Stolen", "rule_set_id": rule["id"]}).status_code == 404
    assert env.client.delete(f"/api/ingestion/sources/{source['id']}", headers=other).status_code == 404
    assert env.client.get("/api/ingestion/sources", headers=other).json() == []
    calls, saved = [], []
    def get_object(**kw):
        calls.append(kw)
        return {"ContentLength": 5, "Body": io.BytesIO(b"id\n1\n")}
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: SimpleNamespace(get_object=get_object))
    monkeypatch.setattr(ingestion, "get_storage", lambda: SimpleNamespace(save_bytes=lambda k, b: saved.append((k, b))))
    event = ingestion.ObjectEvent(bucket="test-data", key=source["prefix"] + "data.csv", version_id="v1")
    first = ingestion.ingest(event)
    assert ingestion.ingest(event) == {"job_id": first["job_id"], "duplicate": True}
    assert len(calls) == 1 and calls[0]["VersionId"] == "v1"
    event.version_id = "v2"
    second = ingestion.ingest(event)
    assert second["job_id"] != first["job_id"]
    assert len(saved) == 2 and all(k.startswith("uploads/" + owner) for k, b in saved)
    env.client.delete(f"/api/ingestion/sources/{source['id']}", headers=headers)
    event.version_id = "v3"
    assert "ignored" in ingestion.ingest(event)
    with env.scope() as db:
        assert len(db.scalars(select(Job)).all()) == 2


def test_ingestion_invalid_bucket_and_path(feature_env):
    for bucket, key, version in [("wrong", "incoming/a/test.csv", "v"), ("test-data", "uploads/a/test.csv", "v"),
                                 ("test-data", "incoming/not-a-uuid/test.csv", "v"), ("test-data", "incoming/a/test.csv", "null")]:
        with pytest.raises(Exception):
            ingestion.event_source(ingestion.ObjectEvent(bucket=bucket, key=key, version_id=version))


def test_callback_requires_signature(feature_env):
    assert feature_env.client.post("/api/ingestion/events", json={"bucket": "test-data", "key": "x", "version_id": "v"}).status_code == 401


def test_english_rule_generation_endpoint_removed(feature_env):
    assert feature_env.client.post("/api/rule-sets/draft", json={}).status_code == 404
