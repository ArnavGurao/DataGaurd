"""Generated artifact tests (README 12.2)."""

from __future__ import annotations

import csv
import io
import json

import pytest

from app.services.reports import (
    ERROR_CODES_COLUMN,
    ERROR_MESSAGES_COLUMN,
    RECORD_NUMBER_COLUMN,
    REPORT_SCHEMA_VERSION,
    build_rejected_csv,
    build_report,
    build_report_json,
    build_valid_csv,
)
from app.services.rules import CODE_DUPLICATE_VALUE, CODE_NUMERIC_RANGE
from app.services.validator import parse_csv, validate
from conftest import LIMITS


def read_csv(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("utf-8"))))


def analyze(data: bytes, rules):
    parsed = parse_csv(data, **LIMITS)
    return parsed, validate(parsed, rules, error_preview_limit=100)


def test_valid_export_keeps_original_columns_and_values(sample, demo_rules):
    parsed, result = analyze(sample("students_dirty.csv"), demo_rules)
    rows = read_csv(build_valid_csv(parsed, result))

    assert rows[0] == ["student_id", "name", "age", "department"]
    assert rows[1:] == [["S001", "Asha", "20", "COMP"], ["S006", "Neha", "23", "IT"]]


def test_rejected_export_carries_the_three_annotation_columns(sample, demo_rules):
    parsed, result = analyze(sample("students_dirty.csv"), demo_rules)
    rows = read_csv(build_rejected_csv(parsed, result))

    assert rows[0] == [
        "student_id",
        "name",
        "age",
        "department",
        RECORD_NUMBER_COLUMN,
        ERROR_CODES_COLUMN,
        ERROR_MESSAGES_COLUMN,
    ]
    assert len(rows) == 5  # header plus four rejected rows

    # Rejected records are 2, 3, 4 and 5; record 3 (S003/Rohan) fails twice, so
    # both codes appear in one cell. Valid records 1 and 6 are absent entirely.
    record_3 = rows[2]
    assert record_3[0] == "S003"
    assert record_3[2] == "15"
    assert record_3[4] == "3"
    assert set(record_3[5].split("|")) == {CODE_DUPLICATE_VALUE, CODE_NUMERIC_RANGE}
    assert "|" in record_3[6]


def test_record_numbers_are_logical_not_physical():
    """A quoted newline must not inflate the record number (README 12.2)."""
    data = b'student_id,name\nS001,"line one\nline two"\nS002,\n'
    rules = _simple_rules()
    parsed, result = analyze(data, rules)
    rows = read_csv(build_rejected_csv(parsed, result))

    assert rows[1][0] == "S002"
    assert rows[1][2] == "2"  # the second *record*, though the third physical line


def test_rejected_export_is_written_even_when_nothing_is_rejected(sample, demo_rules):
    parsed, result = analyze(sample("students_good.csv"), demo_rules)
    rows = read_csv(build_rejected_csv(parsed, result))

    assert len(rows) == 1  # header only
    assert rows[0][-1] == ERROR_MESSAGES_COLUMN


def test_valid_export_is_written_even_when_everything_is_rejected(sample, demo_rules):
    parsed, result = analyze(sample("students_missing_column.csv"), demo_rules)
    rows = read_csv(build_valid_csv(parsed, result))

    assert len(rows) == 1  # header only, predictable artifact set
    assert rows[0] == ["student_id", "name", "age"]


def test_report_describes_the_audit(sample, demo_rules):
    parsed, result = analyze(sample("students_dirty.csv"), demo_rules)
    report = build_report(
        job_id="11111111-2222-3333-4444-555555555555",
        rules=demo_rules.to_json(),
        result=result,
        processing_seconds=1.23456,
    )

    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["job_id"] == "11111111-2222-3333-4444-555555555555"
    assert report["counts"] == {
        "total_rows": 6,
        "valid_rows": 2,
        "rejected_rows": 4,
        "valid_percentage": 33.33,
    }
    assert report["rules"] == demo_rules.to_json()
    assert report["processing_seconds"] == 1.235
    assert report["generated_at"].endswith("Z")
    assert report["error_preview_truncated"] is False


def test_report_json_is_parseable_and_free_of_nan():
    rules = _simple_rules()
    parsed, result = analyze(b"student_id,name\nS001,\n", rules)
    payload = build_report_json(
        build_report(job_id="job", rules=rules.to_json(), result=result, processing_seconds=0.0)
    )

    # allow_nan=False means a stray NaN would raise here rather than emit
    # invalid JSON that a strict parser rejects.
    parsed_json = json.loads(payload)
    assert parsed_json["counts"]["total_rows"] == 1


def test_error_preview_truncation_is_reported():
    rules = _simple_rules()
    data = b"student_id,name\n" + b"".join(b"S%03d,\n" % i for i in range(30))
    parsed = parse_csv(data, **LIMITS)
    result = validate(parsed, rules, error_preview_limit=10)
    report = build_report(
        job_id="job", rules=rules.to_json(), result=result, processing_seconds=0.0
    )

    assert len(report["error_preview"]) == 10
    assert report["error_preview_truncated"] is True


def _simple_rules():
    from app.services.rules import RuleSet

    return RuleSet.from_payload(
        {"required_columns": ["student_id", "name"], "required_values": ["name"]}
    )
