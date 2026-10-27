"""Storage interface plus the local implementation (README 12.1).

Owner B defines this interface and the key layout; owner C adds the S3
implementation behind ``S3Storage``. Nothing here accepts a user-supplied path
or bucket: keys are always derived from server-side IDs.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import UUID

from ..config import get_settings


class StorageError(RuntimeError):
    """Storage is configured but unusable. Surfaces as HTTP 503."""


def upload_key(owner_id: UUID | str, dataset_id: UUID | str) -> str:
    """Key for an uploaded original. Deterministic so retries overwrite."""
    return f"uploads/{owner_id}/{dataset_id}/original.csv"


def output_key(owner_id: UUID | str, job_id: UUID | str, kind: str) -> str:
    """Key for a generated artifact. Deterministic across worker retries."""
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"Unknown artifact kind: {kind}")
    return f"outputs/{owner_id}/{job_id}/{kind}.{'json' if kind == 'report' else 'csv'}"


#: Artifacts a completed job can offer for download (README 9.7).
ARTIFACT_KINDS: tuple[str, ...] = ("original", "valid", "rejected", "report")


def artifact_filename(original_name: str, kind: str) -> str:
    """The ``attachment`` filename a download should carry."""
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"Unknown artifact kind: {kind}")
    stem = Path(original_name).stem or "dataset"
    suffix = "json" if kind == "report" else "csv"
    label = {"original": "original", "valid": "valid", "rejected": "rejected", "report": "report"}[kind]
    return f"{stem}_{label}.{suffix}"


class StorageBackend(ABC):
    """The seam between application logic and where bytes actually live."""

    @abstractmethod
    def save_bytes(self, key: str, data: bytes) -> None:
        """Write ``data`` at ``key``, replacing any existing object."""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes:
        """Read ``key``. Raises ``FileNotFoundError`` when absent."""

    @abstractmethod
    def delete_object(self, key: str) -> None:
        """Delete ``key``. Deleting a missing key is not an error."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Whether ``key`` is present."""

    @abstractmethod
    def get_download_url(self, key: str, filename: str, ttl_seconds: int) -> str:
        """A short-lived URL the browser can fetch directly.

        The URL is a temporary bearer capability: never log it or put it in a
        screenshot (README 12.3).
        """


class LocalStorage(StorageBackend):
    """Files under ``backend/runtime/storage``, which is git-ignored."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or get_settings().storage_root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, key: str) -> Path:
        """Resolve ``key`` under the root, refusing anything that escapes it.

        Keys are generated internally, but this is the boundary that makes a
        traversal bug a loud failure instead of a write outside the sandbox.
        """
        candidate = (self._root / key).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise StorageError(f"Refusing storage key outside the storage root: {key}")
        return candidate

    def save_bytes(self, key: str, data: bytes) -> None:
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        return self.path_for(key).read_bytes()

    def delete_object(self, key: str) -> None:
        target = self.path_for(key)
        try:
            target.unlink()
        except FileNotFoundError:
            return

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def get_download_url(self, key: str, filename: str, ttl_seconds: int) -> str:
        """Local mode returns a signed API URL; see ``app.auth``.

        Signing lives with the caller because it needs the JWT secret, so this
        method only reports that a local URL is used rather than an S3 one.
        """
        raise NotImplementedError(
            "LocalStorage downloads are issued as signed API URLs by the jobs router."
        )

    def delete_tree(self, key_prefix: str) -> None:
        """Remove a directory subtree. Used only by tests and local cleanup."""
        target = self.path_for(key_prefix)
        if target.is_dir():
            shutil.rmtree(target)


class S3Storage(StorageBackend):
    """AWS S3 implementation (README 12.1).

    Boto3 picks up temporary credentials from the EC2 instance role; no access
    keys are read from configuration or embedded anywhere (README 2, 18).
    """

    def __init__(self, bucket: str | None = None, region: str | None = None) -> None:
        settings = get_settings()
        self._bucket = bucket or settings.s3_bucket_name
        self._region = region or settings.aws_region
        if not self._bucket:
            raise StorageError("S3_BUCKET_NAME must be set when STORAGE_MODE=s3.")

    @property
    def bucket(self) -> str:
        return self._bucket  # type: ignore[return-value]

    def _client(self):
        import boto3  # lazy: local development needs no boto3 session
        from botocore.config import Config

        return boto3.client(
            "s3",
            region_name=self._region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "virtual"},
                connect_timeout=5,
                read_timeout=30,
                retries={"mode": "standard", "max_attempts": 3},
            ),
        )

    def save_bytes(self, key: str, data: bytes) -> None:
        content_type = "application/json" if key.endswith(".json") else "text/csv"
        self._client().put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def read_bytes(self, key: str) -> bytes:
        try:
            response = self._client().get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                raise FileNotFoundError(key) from exc
            raise StorageError(f"Could not read S3 object: {key}") from exc
        return response["Body"].read()

    def delete_object(self, key: str) -> None:
        try:
            self._client().delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError(f"Could not delete S3 object: {key}") from exc

    def exists(self, key: str) -> bool:
        try:
            self._client().head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return False
            raise StorageError(f"Could not check S3 object: {key}") from exc
        return True

    def get_download_url(self, key: str, filename: str, ttl_seconds: int) -> str:
        if ttl_seconds <= 0:
            raise ValueError("Download URL TTL must be positive.")
        return self._client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{Path(filename).name}"',
            },
            ExpiresIn=ttl_seconds,
        )


def get_storage() -> StorageBackend:
    """Build the configured backend.

    There is no silent fallback: a failure to construct S3 storage must not
    quietly write to the local disk in production (README 12.1).
    """
    settings = get_settings()
    if settings.storage_mode == "s3":
        return S3Storage()
    return LocalStorage()
