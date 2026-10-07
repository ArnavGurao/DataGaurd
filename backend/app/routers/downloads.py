"""Signed download endpoint used in local mode (README 10.6, 12.3).

AWS mode hands the browser a presigned S3 URL. Local mode has no presigner, so
it issues a signed API URL instead. Either way the browser can fetch the artifact
with a plain navigation — no ``Authorization`` header — which is why the signed
token, not the session, is the capability here.

The token is deliberately *not* trusted for anything except identifying which
artifact was requested: the caller re-checks ownership and availability against
the database on every request, and derives the storage key from server-side
metadata.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import decode_download_token
from ..config import get_settings
from ..database import get_db
from ..errors import CODE_NOT_FOUND, ApiError
from ..models import Job
from ..services.storage import ARTIFACT_KINDS, artifact_filename, get_storage

router = APIRouter(prefix="/api/downloads", tags=["downloads"])

_MEDIA_TYPES = {"report": "application/json"}
_DEFAULT_MEDIA_TYPE = "text/csv"


@router.get("/{token}")
def download_with_token(token: str, db: Session = Depends(get_db)) -> Response:
    if get_settings().storage_mode != "local":
        # In S3 mode downloads never come back through this API.
        raise ApiError(404, CODE_NOT_FOUND, "Not found.")

    payload = decode_download_token(token)

    try:
        user_id = uuid.UUID(payload["sub"])
        job_id = uuid.UUID(payload["job_id"])
        kind = str(payload["kind"])
    except (KeyError, ValueError) as exc:
        raise ApiError(401, "UNAUTHORIZED", "Invalid download token.") from exc

    if kind not in ARTIFACT_KINDS:
        raise ApiError(404, CODE_NOT_FOUND, "Unknown artifact.")

    # Ownership is re-established from the database, not from the token's claims
    # alone, so a leaked token cannot outlive the job it points at.
    job = db.execute(
        select(Job).where(Job.id == job_id, Job.owner_id == user_id)
    ).scalar_one_or_none()
    if job is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not found.")

    if kind == "original":
        key = job.dataset.storage_key
    else:
        entry = (job.artifacts_json or {}).get(kind)
        if not entry:
            raise ApiError(404, CODE_NOT_FOUND, "That artifact is not available for this job.")
        key = entry["key"]

    try:
        data = get_storage().read_bytes(key)
    except FileNotFoundError as exc:
        raise ApiError(404, CODE_NOT_FOUND, "That artifact is no longer available.") from exc

    filename = artifact_filename(job.dataset.original_name, kind)
    return Response(
        content=data,
        media_type=_MEDIA_TYPES.get(kind, _DEFAULT_MEDIA_TYPE),
        headers={
            # Both forms: the quoted one for older clients, RFC 5987 for
            # anything non-ASCII.
            "Content-Disposition": (
                f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename)}'
            )
        },
    )
