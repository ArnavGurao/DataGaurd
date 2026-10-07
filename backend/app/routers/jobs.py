"""Job submission, status, listing, reports and download authorization (README 9.7, 12.3).

The upload handler does the cheap, certain checks — ownership, size, extension,
header sanity — then commits a ``PENDING_DISPATCH`` job and returns 202. Row
validation belongs to the worker, so a slow or dirty file never delays the
browser.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..auth import create_download_token, get_current_user
from ..config import get_settings
from ..database import get_db
from ..errors import (
    CODE_FILE_TOO_LARGE,
    CODE_NOT_FOUND,
    CODE_REPORT_NOT_READY,
    CODE_STORAGE_UNAVAILABLE,
    ApiError,
)
from ..models import Dataset, Job, JobStatus, RuleSet as RuleSetModel, User
from ..schemas import AcceptedJobOut, DownloadUrlOut, JobErrorOut, JobListOut, JobOut, SummaryOut
from ..services.storage import (
    ARTIFACT_KINDS,
    StorageError,
    artifact_filename,
    get_storage,
    output_key,
    upload_key,
)
from ..services.validator import ValidationInputError, inspect_headers

logger = logging.getLogger("dataguard.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

#: Every route that serializes a job needs the dataset's filename and the rule
#: set's name for display. Loading them here keeps a 100-job page at one query
#: instead of one per row.
_JOB_METADATA = (joinedload(Job.dataset), joinedload(Job.rule_set))

#: Read the upload in bounded chunks so an oversized body is refused mid-stream
#: rather than after it has all been buffered.
_CHUNK_BYTES = 64 * 1024


@router.post("", response_model=AcceptedJobOut, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    title: str = Form(...),
    rule_set_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AcceptedJobOut:
    settings = get_settings()

    clean_title = title.strip()
    if not clean_title:
        raise ApiError(422, "VALIDATION_ERROR", "A title is required.")

    display_name = (file.filename or "").strip()
    if not display_name.lower().endswith(".csv"):
        raise ApiError(422, "VALIDATION_ERROR", "Upload a .csv file.")

    # Ownership is checked before anything is stored or written.
    rule_set = db.execute(
        select(RuleSetModel).where(
            RuleSetModel.id == rule_set_id, RuleSetModel.owner_id == user.id
        )
    ).scalar_one_or_none()
    if rule_set is None:
        raise ApiError(404, CODE_NOT_FOUND, "Rule set not found.")

    data = await _read_limited(file, settings.max_upload_bytes)

    try:
        inspect_headers(data, max_columns=settings.max_csv_columns)
    except ValidationInputError as exc:
        raise ApiError(422, exc.code, exc.message) from exc

    dataset_id = uuid.uuid4()
    job_id = uuid.uuid4()
    key = upload_key(user.id, dataset_id)

    storage = get_storage()
    try:
        storage.save_bytes(key, data)
    except (StorageError, OSError) as exc:
        logger.error("Storing the upload failed for dataset %s.", dataset_id, exc_info=exc)
        # Nothing was committed, so no job exists to clean up (README 13.7).
        raise ApiError(
            503, CODE_STORAGE_UNAVAILABLE, "Storage is unavailable. Try again shortly."
        ) from exc

    try:
        dataset = Dataset(
            id=dataset_id,
            owner_id=user.id,
            original_name=_sanitize_display_name(display_name),
            storage_key=key,
            size_bytes=len(data),
        )
        job = Job(
            id=job_id,
            owner_id=user.id,
            dataset_id=dataset_id,
            rule_set_id=rule_set.id,
            title=clean_title[:200],
            # Frozen at submission: editing the rule set later must not change
            # what this audit meant (README 9.5).
            rules_snapshot=rule_set.rules_json,
            status=JobStatus.PENDING_DISPATCH.value,
        )
        db.add(dataset)
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        # The object exists but nothing references it; delete this exact key
        # rather than leaving an orphan behind.
        _cleanup_orphan(storage, key, dataset_id)
        raise

    return AcceptedJobOut(job_id=job.id, status=job.status)


@router.get("", response_model=JobListOut)
def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobListOut:
    total = db.execute(
        select(func.count()).select_from(Job).where(Job.owner_id == user.id)
    ).scalar_one()

    jobs = (
        db.execute(
            select(Job)
            .where(Job.owner_id == user.id)
            .options(*_JOB_METADATA)
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    return JobListOut(
        items=[_to_out(job) for job in jobs], total=total, limit=limit, offset=offset
    )


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    return _to_out(_owned_job(db, user, job_id))


@router.get("/{job_id}/report")
def get_report(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    job = _owned_job(db, user, job_id)

    if job.status != JobStatus.COMPLETED.value:
        # 409, not 404: the report is expected to exist later.
        raise ApiError(409, CODE_REPORT_NOT_READY, "The report is not ready yet.")

    key = _artifact_key(job, "report")
    try:
        payload = get_storage().read_bytes(key)
    except FileNotFoundError as exc:
        raise ApiError(404, CODE_NOT_FOUND, "The report is no longer available.") from exc

    return JSONResponse(content=json.loads(payload))


@router.get("/{job_id}/downloads/{kind}", response_model=DownloadUrlOut)
def get_download_url(
    job_id: uuid.UUID,
    kind: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DownloadUrlOut:
    """Issue a short-lived URL for one artifact, after an ownership check."""
    if kind not in ARTIFACT_KINDS:
        raise ApiError(404, CODE_NOT_FOUND, f"Unknown artifact '{kind}'.")

    job = _owned_job(db, user, job_id)
    _require_available(job, kind)

    settings = get_settings()
    ttl = settings.download_url_ttl_seconds
    filename = artifact_filename(job.dataset.original_name, kind)

    if settings.storage_mode == "s3":
        # Owner C: presigned S3 GET with an attachment filename.
        url = get_storage().get_download_url(_artifact_key(job, kind), filename, ttl)
    else:
        # Local mode: a signed URL back to this API, scoped to one artifact.
        token = create_download_token(
            user_id=user.id, job_id=job.id, kind=kind, ttl_seconds=ttl
        )
        url = f"/api/downloads/{token}"

    return DownloadUrlOut(url=url, filename=filename, expires_in=ttl)


def _owned_job(db: Session, user: User, job_id: uuid.UUID) -> Job:
    """Load a job by id *and* owner.

    A job belonging to someone else is reported as missing, so the endpoint
    cannot be used to confirm that another user's job exists (README 9.6).
    """
    job = db.execute(
        select(Job).where(Job.id == job_id, Job.owner_id == user.id).options(*_JOB_METADATA)
    ).scalar_one_or_none()
    if job is None:
        raise ApiError(404, CODE_NOT_FOUND, "Job not found.")
    return job


def _artifact_key(job: Job, kind: str) -> str:
    """Derive an artifact's storage key from server-side metadata only."""
    if kind == "original":
        return job.dataset.storage_key

    entry = (job.artifacts_json or {}).get(kind)
    if not entry:
        raise ApiError(404, CODE_NOT_FOUND, "That artifact is not available for this job.")
    return entry["key"]


def _require_available(job: Job, kind: str) -> None:
    if kind not in _available_downloads(job):
        raise ApiError(404, CODE_NOT_FOUND, "That artifact is not available for this job.")


def _available_downloads(job: Job) -> list[str]:
    """Which artifacts really exist right now.

    The original is downloadable from the moment the job is accepted; the
    generated outputs appear only once the worker has written them.
    """
    available = ["original"]
    artifacts = job.artifacts_json or {}
    available.extend(kind for kind in ARTIFACT_KINDS if kind != "original" and kind in artifacts)
    return available


def _to_out(job: Job) -> JobOut:
    summary = job.summary_json
    error = None
    if job.error_code:
        error = JobErrorOut(
            code=job.error_code, message=job.error_message or "", failure_kind=job.failure_kind
        )

    # Both relationships are eager-loaded by every caller (_JOB_METADATA), so
    # this costs no extra query. They can still be absent on a job whose dataset
    # or rule set was removed, which is why the fields are nullable.
    dataset = job.dataset
    rule_set = job.rule_set

    return JobOut(
        job_id=job.id,
        title=job.title,
        original_name=dataset.original_name if dataset else None,
        rule_set_id=job.rule_set_id,
        rule_set_name=rule_set.name if rule_set else None,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        summary=SummaryOut(**summary) if summary else None,
        available_downloads=_available_downloads(job),
        error=error,
    )


async def _read_limited(upload: UploadFile, limit_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit_bytes:
            raise ApiError(
                413,
                CODE_FILE_TOO_LARGE,
                f"Upload a CSV of {limit_bytes // (1024 * 1024)} MiB or less.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _sanitize_display_name(name: str) -> str:
    """Keep the original name as metadata only — never as a storage path."""
    cleaned = name.replace("\\", "/").split("/")[-1].strip()
    return (cleaned or "dataset.csv")[:255]


def _cleanup_orphan(storage, key: str, dataset_id: uuid.UUID) -> None:
    try:
        storage.delete_object(key)
    except Exception:
        # Worth knowing about during the demo lifecycle, but not worth failing
        # the request over: the user's upload already failed (README 13.7).
        logger.warning("Could not clean up orphaned object for dataset %s.", dataset_id, exc_info=True)
