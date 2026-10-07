"""Typed application settings.

Local development reads ``backend/.env`` (README 9.4). Deployed environments
(``APP_ENV=aws``) load secrets from Parameter Store ``SecureString`` instead
and never fall back to committed defaults, so ``.env`` is skipped entirely in
that mode. Secrets are never logged or returned by health endpoints.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_STORAGE_ROOT = "runtime/storage"

# Replaced from .env.example; refusing it prevents a default secret shipping.
PLACEHOLDER_SECRETS = {
    "replace-with-random-local-secret",
    "replace-with-local-password",
}

AppEnv = Literal["local", "aws"]


class Settings(BaseSettings):
    """Runtime configuration shared by the API and the worker."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = "local"

    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "dataguard"
    db_user: str = "dataguard_app"
    db_password: str = ""
    db_sslmode: str = "disable"

    jwt_secret: str = ""
    jwt_expire_minutes: int = 30
    jwt_algorithm: Literal["HS256"] = "HS256"

    # Storage and queue adapters are selected here; AWS modes are owner C's.
    storage_mode: Literal["local", "s3"] = "local"
    queue_mode: Literal["database", "sqs"] = "database"
    local_storage_root: str = DEFAULT_LOCAL_STORAGE_ROOT

    # Baseline limits (README 1.2).
    max_upload_bytes: int = 5 * 1024 * 1024
    max_csv_rows: int = 20_000
    max_csv_columns: int = 50

    # Worker, lease and reporting behaviour (README 12.2, 13.3, 13.4).
    lease_seconds: int = 120
    publisher_interval_seconds: float = 5.0
    database_queue_poll_seconds: float = 3.0
    error_preview_limit: int = 100

    # AWS mode only (owner: C).
    aws_region: str = "us-east-1"
    s3_bucket_name: str | None = None
    sqs_queue_url: str | None = None
    sns_topic_arn: str | None = None
    ssm_parameter_prefix: str = "/dataguard"
    download_url_ttl_seconds: int = 60

    @model_validator(mode="after")
    def _validate_required(self) -> "Settings":
        missing: list[str] = []

        if not self.jwt_secret or self.jwt_secret in PLACEHOLDER_SECRETS:
            missing.append("JWT_SECRET")
        if not self.db_password or self.db_password in PLACEHOLDER_SECRETS:
            missing.append("DB_PASSWORD")

        # AWS selections need their target resource named; there is no silent
        # fallback to local storage or the database queue (README 12.1).
        if self.storage_mode == "s3" and not self.s3_bucket_name:
            missing.append("S3_BUCKET_NAME")
        if self.queue_mode == "sqs" and not self.sqs_queue_url:
            missing.append("SQS_QUEUE_URL")

        if missing:
            raise ValueError(
                "Missing or placeholder settings: "
                + ", ".join(missing)
                + ". Copy backend/.env.example to backend/.env and fill it in."
            )

        # An AWS deployment must name its cloud adapters explicitly. Because
        # STORAGE_MODE and QUEUE_MODE default to their local values, forgetting
        # them in the cloud would not fail — it would quietly write uploads to a
        # container filesystem that is discarded on the next deploy, and use the
        # jobs table as a queue that no other replica can see. README 12.1
        # forbids that silent fallback, so refuse to start instead.
        if self.app_env != "local":
            required_modes = {"STORAGE_MODE": (self.storage_mode, "s3"), "QUEUE_MODE": (self.queue_mode, "sqs")}
            wrong = [f"{name}={want}" for name, (have, want) in required_modes.items() if have != want]
            if wrong:
                raise ValueError(
                    f"APP_ENV={self.app_env} requires the cloud adapters: set "
                    + " and ".join(wrong)
                    + ". AWS settings come from Parameter Store, not backend/.env."
                )

        if self.apply_ssl_requirement() is None:
            raise ValueError("DB_SSLMODE must be a non-empty value.")
        return self

    def apply_ssl_requirement(self) -> str:
        """Placeholder hook kept explicit so the sslmode default stays visible."""
        return self.db_sslmode

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def database_url(self) -> URL:
        """Build the URL via SQLAlchemy so special characters are escaped.

        Manual string assembly breaks on passwords containing ``@`` or ``:``.
        """
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"sslmode": self.db_sslmode},
        )

    @property
    def storage_root(self) -> Path:
        """Absolute local storage root; relative values resolve under backend/."""
        root = Path(self.local_storage_root)
        return root if root.is_absolute() else (BACKEND_DIR / root).resolve()


def _app_env_from_environment() -> AppEnv:
    """Read APP_ENV before Settings exists, since it selects the settings source."""
    raw = os.environ.get("APP_ENV", "local").strip().lower()
    return "aws" if raw == "aws" else "local"


def _apply_parameter_store(settings_env: dict[str, str]) -> None:
    """Load SecureString parameters into the environment for AWS mode.

    Parameter names map to settings fields by their last path segment, so
    ``/dataguard/jwt_secret`` sets ``JWT_SECRET``.
    """
    import boto3  # Imported lazily so local work needs no AWS dependencies.

    prefix = settings_env.get("SSM_PARAMETER_PREFIX", "/dataguard")
    region = settings_env.get("AWS_REGION", "us-east-1")

    client = boto3.client("ssm", region_name=region)
    paginator = client.get_paginator("get_parameters_by_path")
    pages = paginator.paginate(Path=prefix, Recursive=True, WithDecryption=True)

    for page in pages:
        for parameter in page.get("Parameters", []):
            name = parameter["Name"].rsplit("/", 1)[-1].upper()
            os.environ.setdefault(name, parameter["Value"])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Call ``get_settings.cache_clear()`` in tests."""
    if _app_env_from_environment() == "aws":
        _apply_parameter_store(dict(os.environ))
        # Skip .env entirely: deployed secrets come from Parameter Store.
        return Settings(_env_file=None)
    return Settings(_env_file=BACKEND_DIR / ".env")
