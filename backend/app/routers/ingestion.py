"""Account-owned S3 folders and authenticated, idempotent Lambda callbacks."""
import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..auth import get_current_user
from ..config import get_settings
from ..database import get_db, session_scope
from ..errors import ApiError
from ..models import Dataset, IngestionReceipt, IngestionSource, Job, RuleSet, User
from ..services.storage import get_storage, upload_key
from ..services.validator import inspect_headers, ValidationInputError

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


class SourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rule_set_id: uuid.UUID


class ObjectEvent(BaseModel):
    bucket: str = Field(min_length=1, max_length=255)
    key: str = Field(min_length=1, max_length=1024)
    version_id: str = Field(min_length=1, max_length=1024)


def source_out(source):
    bucket = get_settings().s3_bucket_name
    prefix = f"incoming/{source.id}/"
    return {"id": str(source.id), "name": source.name, "rule_set_id": str(source.rule_set_id),
            "enabled": bool(source.enabled), "bucket": bucket, "prefix": prefix,
            "uri": f"s3://{bucket}/{prefix}"}


@router.get("/sources")
def list_sources(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [source_out(s) for s in db.scalars(select(IngestionSource).where(IngestionSource.owner_id == user.id).order_by(IngestionSource.created_at.desc()))]


@router.post("/sources", status_code=201)
def create_source(payload: SourceRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    if settings.storage_mode != "s3" or not settings.ingestion_secret:
        raise ApiError(503, "INGESTION_UNAVAILABLE", "Automatic S3 ingestion is not configured.")
    rule = db.scalar(select(RuleSet).where(RuleSet.id == payload.rule_set_id, RuleSet.owner_id == user.id))
    if not rule:
        raise ApiError(404, "NOT_FOUND", "Rule set not found.")
    if not payload.name.strip():
        raise ApiError(422, "INVALID_NAME", "Give this folder a name.")
    source = IngestionSource(id=uuid.uuid4(), owner_id=user.id, rule_set_id=rule.id, name=payload.name.strip())
    db.add(source)
    db.commit()
    return source_out(source)


@router.delete("/sources/{source_id}")
def disable_source(source_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    source = db.scalar(select(IngestionSource).where(IngestionSource.id == source_id, IngestionSource.owner_id == user.id).with_for_update())
    if not source:
        raise ApiError(404, "NOT_FOUND", "Folder not found.")
    source.enabled = 0
    db.commit()
    return {"disabled": True}


def verify_signature(body, timestamp, signature, secret, now=None):
    try:
        fresh = abs((time.time() if now is None else now) - int(timestamp)) <= 300
    except (ValueError, TypeError):
        fresh = False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not secret or not fresh or not hmac.compare_digest(expected, signature):
        raise ApiError(401, "UNAUTHORIZED", "Invalid ingestion signature.")


def event_source(event):
    parts = event.key.split("/")
    if event.bucket != get_settings().s3_bucket_name or len(parts) != 3 or parts[0] != "incoming" or not parts[2].lower().endswith(".csv") or event.version_id == "null":
        raise ApiError(422, "INVALID_INGESTION_EVENT", "Expected a versioned CSV in an ingestion folder.")
    try:
        return uuid.UUID(parts[1])
    except ValueError as exc:
        raise ApiError(422, "INVALID_INGESTION_EVENT", "Invalid folder identifier.") from exc


def ingest(event):
    settings = get_settings()
    source_id = event_source(event)
    receipt_id = hashlib.sha256(json.dumps([event.bucket, event.key, event.version_id]).encode()).hexdigest()
    with session_scope() as db:
        # Serializes concurrent events for a folder; retries of a committed version reuse its job.
        source = db.scalar(select(IngestionSource).where(IngestionSource.id == source_id).with_for_update())
        if not source or not source.enabled:
            return {"ignored": "Folder does not exist or is disabled."}
        previous = db.get(IngestionReceipt, receipt_id)
        if previous:
            return {"job_id": str(previous.job_id), "duplicate": True}
        user = db.get(User, source.owner_id)
        rule = db.get(RuleSet, source.rule_set_id)
        if not user or not rule or rule.owner_id != user.id or (settings.email_verification_required and not user.email_verified_at):
            return {"ignored": "Folder owner must be verified and own its rule set."}
        import boto3
        from botocore.config import Config
        s3 = boto3.client("s3", region_name=settings.aws_region,
                          config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}))
        obj = s3.get_object(Bucket=event.bucket, Key=event.key, VersionId=event.version_id)
        try:
            if obj["ContentLength"] > settings.max_upload_bytes:
                return {"ignored": "CSV exceeds the 5 MiB upload limit."}
            data = obj["Body"].read(settings.max_upload_bytes + 1)
        finally:
            obj["Body"].close()
        if len(data) > settings.max_upload_bytes:
            return {"ignored": "CSV exceeds the upload limit."}
        try:
            inspect_headers(data, max_columns=settings.max_csv_columns)
        except ValidationInputError as exc:
            return {"ignored": exc.message}
        # Deterministic keys keep retries safe after a failed DB commit. Copy the exact version
        # to ordinary storage so later overwrites of the incoming key cannot change this audit.
        dataset_id = uuid.uuid5(uuid.NAMESPACE_URL, "dataset:" + receipt_id)
        job_id = uuid.uuid5(uuid.NAMESPACE_URL, "job:" + receipt_id)
        key = upload_key(user.id, dataset_id)
        get_storage().save_bytes(key, data)
        filename = event.key.rsplit("/", 1)[-1].replace("\\", "_")[:255]
        db.add(Dataset(id=dataset_id, owner_id=user.id, original_name=filename, storage_key=key, size_bytes=len(data)))
        db.flush()
        db.add(Job(id=job_id, owner_id=user.id, dataset_id=dataset_id, rule_set_id=rule.id,
                   title=f"{source.name}: {filename}"[:200], rules_snapshot=rule.rules_json))
        db.flush()
        db.add(IngestionReceipt(id=receipt_id, source_id=source.id, job_id=job_id))
    return {"job_id": str(job_id), "duplicate": False}


@router.post("/events")
async def receive_event(request: Request):
    body = await request.body()
    if len(body) > 8192:
        raise ApiError(413, "EVENT_TOO_LARGE", "Event body too large.")
    verify_signature(body, request.headers.get("x-dataguard-timestamp", ""),
                     request.headers.get("x-dataguard-signature", ""), get_settings().ingestion_secret)
    try:
        event = ObjectEvent.model_validate_json(body)
    except ValidationError as exc:
        raise ApiError(422, "INVALID_INGESTION_EVENT", "Invalid ingestion event.") from exc
    return await run_in_threadpool(ingest, event)
