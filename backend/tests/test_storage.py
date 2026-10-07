"""Storage key layout and the local backend (README 12.1)."""

from __future__ import annotations

import pytest

from app.services.storage import (
    ARTIFACT_KINDS,
    LocalStorage,
    StorageError,
    artifact_filename,
    output_key,
    upload_key,
)


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    # Passing the root explicitly keeps this test independent of .env.
    return LocalStorage(root=tmp_path)


def test_keys_use_generated_ids_not_user_filenames():
    assert upload_key("owner", "dataset") == "uploads/owner/dataset/original.csv"
    assert output_key("owner", "job", "valid") == "outputs/owner/job/valid.csv"
    assert output_key("owner", "job", "report") == "outputs/owner/job/report.json"


def test_unknown_artifact_kind_is_refused():
    with pytest.raises(ValueError):
        output_key("owner", "job", "everything")
    with pytest.raises(ValueError):
        artifact_filename("data.csv", "everything")


@pytest.mark.parametrize("kind", ARTIFACT_KINDS)
def test_every_artifact_kind_has_a_key_and_filename(kind):
    assert output_key("o", "j", kind)
    assert artifact_filename("students.csv", kind)


def test_artifact_filenames_are_labelled():
    assert artifact_filename("students.csv", "rejected") == "students_rejected.csv"
    assert artifact_filename("students.csv", "report") == "students_report.json"


def test_round_trip(storage):
    key = upload_key("owner", "dataset")
    storage.save_bytes(key, b"a,b\n1,2\n")

    assert storage.exists(key)
    assert storage.read_bytes(key) == b"a,b\n1,2\n"

    storage.delete_object(key)
    assert not storage.exists(key)


def test_delete_is_idempotent(storage):
    storage.delete_object("uploads/nobody/nothing/original.csv")  # must not raise


def test_read_missing_key_raises_file_not_found(storage):
    with pytest.raises(FileNotFoundError):
        storage.read_bytes("uploads/missing/original.csv")


def test_nested_keys_create_parent_directories(storage):
    key = output_key("owner", "job", "valid")
    storage.save_bytes(key, b"x")

    assert storage.read_bytes(key) == b"x"
    assert (storage.root / "outputs" / "owner" / "job" / "valid.csv").is_file()


@pytest.mark.parametrize(
    "key",
    [
        "../escaped.csv",
        "uploads/../../escaped.csv",
        "uploads/owner/../../../etc/passwd",
    ],
)
def test_traversal_outside_the_root_is_refused(storage, key):
    """Keys are generated internally, but the boundary must still hold."""
    with pytest.raises(StorageError):
        storage.path_for(key)


def test_s3_mode_without_a_bucket_is_refused(monkeypatch):
    from app.services import storage as storage_module

    class _Settings:
        storage_mode = "s3"
        s3_bucket_name = None
        aws_region = "us-east-1"

    monkeypatch.setattr(storage_module, "get_settings", lambda: _Settings())

    with pytest.raises(StorageError):
        storage_module.S3Storage()
