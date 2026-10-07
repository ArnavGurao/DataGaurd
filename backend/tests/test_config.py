"""Settings validation tests (README 9.4).

The point of these is that the application refuses to start with a
configuration that would silently misbehave, rather than starting and losing
data later.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

#: A secret that is long enough and is not one of the committed placeholders.
SECRET = "x" * 48


def build(**overrides) -> Settings:
    """Construct Settings from these values alone, ignoring any local .env."""
    values = {"jwt_secret": SECRET, "db_password": "local-password"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_local_mode_uses_the_local_adapters_by_default():
    settings = build()

    assert settings.app_env == "local"
    assert settings.storage_mode == "local"
    assert settings.queue_mode == "database"
    assert settings.is_local is True


def test_placeholder_secrets_are_refused():
    with pytest.raises(ValidationError):
        build(jwt_secret="replace-with-random-local-secret")

    with pytest.raises(ValidationError):
        build(db_password="replace-with-local-password")


@pytest.mark.parametrize(
    ("storage_mode", "queue_mode"),
    [
        ("local", "database"),  # both still local
        ("s3", "database"),  # storage moved, queue did not
        ("local", "sqs"),  # queue moved, storage did not
    ],
)
def test_aws_mode_refuses_the_local_adapters(storage_mode, queue_mode):
    """README 12.1: no silent fallback to local storage or the database queue."""
    with pytest.raises(ValidationError) as excinfo:
        build(
            app_env="aws",
            storage_mode=storage_mode,
            queue_mode=queue_mode,
            s3_bucket_name="dataguard-bucket",
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/dataguard",
        )

    assert "STORAGE_MODE=s3" in str(excinfo.value) or "QUEUE_MODE=sqs" in str(excinfo.value)


def test_aws_mode_with_both_cloud_adapters_is_accepted():
    settings = build(
        app_env="aws",
        storage_mode="s3",
        queue_mode="sqs",
        s3_bucket_name="dataguard-bucket",
        sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/dataguard",
    )

    assert settings.is_local is False
    assert settings.storage_mode == "s3"
    assert settings.queue_mode == "sqs"


def test_cloud_adapters_still_need_their_target_named():
    with pytest.raises(ValidationError) as excinfo:
        build(app_env="aws", storage_mode="s3", queue_mode="sqs")

    message = str(excinfo.value)
    assert "S3_BUCKET_NAME" in message
    assert "SQS_QUEUE_URL" in message
