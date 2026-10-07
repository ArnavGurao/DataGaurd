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
from app.services.rules import (
    CODE_ALLOWED_VALUE,
    CODE_DUPLICATE_VALUE,
    CODE_NUMERIC_RANGE,
    CODE_REQUIRED_VALUE,
)
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
    assert report["summary"] == {
        "total_rows": 6,
        "valid_rows": 2,
        "rejected_rows": 4,
        "valid_percentage": 33.33,
    }
    assert report["rules_snapshot"] == demo_rules.to_json()
    assert report["processing_duration_ms"] == 1235
    assert report["processed_at"].endswith("Z")
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
    assert parsed_json["summary"]["total_rows"] == 1


# --------------------------------------------------- frontend contract guard

#: The keys docs/frontend-api-expectations.md promises the frontend can read.
#: These are asserted as a set rather than one by one so a rename shows up here
#: as a failing contract test instead of as a blank panel in the browser.
FRONTEND_REPORT_KEYS = {
    "schema_version",
    "job_id",
    "processed_at",
    "rules_snapshot",
    "summary",
    "failure_counts",
    "dataset_errors",
    "error_preview",
    "processing_duration_ms",
}


def test_report_exposes_every_key_the_frontend_reads(sample, demo_rules):
    parsed, result = analyze(sample("students_dirty.csv"), demo_rules)
    report = build_report(
        job_id="job", rules=demo_rules.to_json(), result=result, processing_seconds=1.0
    )

    assert FRONTEND_REPORT_KEYS <= set(report)


def test_failure_counts_are_grouped_by_code_and_column(sample, demo_rules):
    """The frontend maps over this array, so it must be a list, not a mapping."""
    parsed, result = analyze(sample("students_dirty.csv"), demo_rules)
    report = build_report(
        job_id="job", rules=demo_rules.to_json(), result=result, processing_seconds=0.0
    )
    counts = report["failure_counts"]

    assert isinstance(counts, list)
    assert all(set(entry) == {"code", "column", "message", "count"} for entry in counts)

    grouped = {(entry["code"], entry["column"]): entry["count"] for entry in counts}
    assert grouped == {
        (CODE_REQUIRED_VALUE, "name"): 1,
        (CODE_DUPLICATE_VALUE, "student_id"): 2,
        (CODE_NUMERIC_RANGE, "age"): 1,
        (CODE_ALLOWED_VALUE, "department"): 1,
    }
    # One row failing two rules appears in both entries, so the total exceeds
    # the four rejected rows (README 8.2) — and every entry carries a message.
    assert sum(entry["count"] for entry in counts) == 5
    assert all(entry["message"] for entry in counts)


def test_failure_counts_omit_codes_that_never_fired(sample, demo_rules):
    parsed, result = analyze(sample("students_good.csv"), demo_rules)
    report = build_report(
        job_id="job", rules=demo_rules.to_json(), result=result, processing_seconds=0.0
    )

    assert report["failure_counts"] == []


def test_error_preview_carries_the_rejected_rows_original_values(sample, demo_rules):
    """The preview table renders one cell per column, so `values` must be a map."""
    parsed, result = analyze(sample("students_dirty.csv"), demo_rules)
    report = build_report(
        job_id="job", rules=demo_rules.to_json(), result=result, processing_seconds=0.0
    )

    for entry in report["error_preview"]:
        assert set(entry) == {"record_number", "values", "errors"}
        assert isinstance(entry["values"], dict)
        assert set(entry["values"]) == {"student_id", "name", "age", "department"}
        assert entry["errors"]

    # Record 3 is S003/Rohan: the duplicate id whose age is also out of range.
    third = next(e for e in report["error_preview"] if e["record_number"] == 3)
    assert third["values"]["student_id"] == "S003"
    assert third["values"]["age"] == "15"
    assert len(third["errors"]) == 2


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
