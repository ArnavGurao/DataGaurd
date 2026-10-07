"""Validation engine tests (README 11.5).

These cover the behaviour the whole project is graded on: accurate dirty-file
counts, the duplicate semantics, schema failures, preserved identifiers and
malformed input.
"""

from __future__ import annotations

import pytest

from app.services.rules import (
    CODE_ALLOWED_VALUE,
    CODE_DUPLICATE_VALUE,
    CODE_MISSING_COLUMN,
    CODE_NUMERIC_RANGE,
    CODE_REQUIRED_VALUE,
    RuleSet,
)
from app.services.validator import (
    ValidationInputError,
    parse_csv,
    validate,
)
from conftest import LIMITS, csv_bytes

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def run(data: bytes, rules: RuleSet, **limit_overrides):
    limits = {**LIMITS, **limit_overrides}
    return validate(parse_csv(data, **limits), rules, error_preview_limit=100)


# --------------------------------------------------------------- dirty file


def test_dirty_file_matches_documented_counts(sample, demo_rules):
    """README 11.3: 6 rows, 2 valid, 4 rejected, 33.33%."""
    result = run(sample("students_dirty.csv"), demo_rules)

    assert result.total_rows == 6
    assert result.valid_rows == 2
    assert result.rejected_rows == 4
    assert result.valid_percentage == 33.33


def test_dirty_file_failure_counts_are_per_rule(sample, demo_rules):
    """Five failures across four rejected rows: one row fails twice."""
    result = run(sample("students_dirty.csv"), demo_rules)

    assert result.failure_counts[CODE_REQUIRED_VALUE] == 1
    assert result.failure_counts[CODE_DUPLICATE_VALUE] == 2
    assert result.failure_counts[CODE_NUMERIC_RANGE] == 1
    assert result.failure_counts[CODE_ALLOWED_VALUE] == 1
    assert result.failure_counts[CODE_MISSING_COLUMN] == 0
    # The documented point: per-rule counts do not sum to rejected_rows.
    assert sum(result.failure_counts.values()) == 5


def test_row_failing_two_rules_keeps_both_explanations(sample, demo_rules):
    """S003/Rohan is both the duplicate ID and the out-of-range age."""
    result = run(sample("students_dirty.csv"), demo_rules)

    codes = {error.code for error in result.row_errors[2]}
    assert codes == {CODE_DUPLICATE_VALUE, CODE_NUMERIC_RANGE}


def test_both_duplicate_occurrences_are_rejected(sample, demo_rules):
    """keep=False semantics: the first S003 is rejected too, not kept."""
    result = run(sample("students_dirty.csv"), demo_rules)

    # Records 3 and 4 are the two S003 rows (1-based record numbers).
    assert result.valid_mask[2] is False
    assert result.valid_mask[3] is False
    for position in (2, 3):
        assert CODE_DUPLICATE_VALUE in {e.code for e in result.row_errors[position]}


def test_good_file_is_fully_valid(sample, demo_rules):
    result = run(sample("students_good.csv"), demo_rules)

    assert result.total_rows == 6
    assert result.valid_rows == 6
    assert result.rejected_rows == 0
    assert result.valid_percentage == 100.0
    assert not result.has_rejects


# ------------------------------------------------------------ schema faults


def test_missing_column_is_a_completed_audit_not_a_failure(sample, demo_rules):
    """README 11.2: a missing column rejects every row but the audit still ran."""
    result = run(sample("students_missing_column.csv"), demo_rules)

    assert result.valid_rows == 0
    assert result.rejected_rows == result.total_rows
    assert [error["code"] for error in result.dataset_errors] == [CODE_MISSING_COLUMN]
    assert result.dataset_errors[0]["column"] == "department"

    # Every row carries the reason, so the rejected export explains itself.
    for errors in result.row_errors:
        assert CODE_MISSING_COLUMN in {error.code for error in errors}


def test_missing_column_reports_every_absent_column():
    rules = RuleSet.from_payload(
        {"required_columns": ["a", "b", "c"], "required_values": ["a", "b", "c"]}
    )
    result = run(csv_bytes("a", "1"), rules)

    assert {error["column"] for error in result.dataset_errors} == {"b", "c"}


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "EMPTY_DATASET"),
        (csv_bytes("student_id,name,age,department"), "EMPTY_DATASET"),
        (csv_bytes("student_id,name,name,department", "S001,A,A,COMP"), "DUPLICATE_HEADERS"),
        (csv_bytes("student_id,,age", "S001,A,20"), "INVALID_HEADER"),
        (csv_bytes("student_id,__dg_errors", "S001,x"), "RESERVED_HEADER"),
        (csv_bytes("student_id,name,age", "S001,Asha"), "MALFORMED_CSV"),
    ],
)
def test_structural_faults_are_input_errors(data, code):
    """These fail the job; they are not 'bad data' results (README 13.5)."""
    with pytest.raises(ValidationInputError) as excinfo:
        parse_csv(data, **LIMITS)

    assert excinfo.value.code == code


def test_bom_is_stripped():
    data = b"\xef\xbb\xbfstudent_id,name,age,department\nS001,Asha,20,COMP\n"
    parsed = parse_csv(data, **LIMITS)

    assert parsed.headers[0] == "student_id"


def test_stray_bom_does_not_rename_a_header():
    """utf-8-sig only strips a BOM at offset 0; a mid-file one must not survive.

    Left in place it makes the header '\\ufeffid', which is not the configured
    column, so every row would be rejected as MISSING_COLUMN.
    """
    data = b"\n\xef\xbb\xbfstudent_id,name\nS001,Asha\n"
    parsed = parse_csv(data, **LIMITS)

    assert parsed.headers == ("student_id", "name")


def test_non_utf8_is_refused():
    with pytest.raises(ValidationInputError) as excinfo:
        parse_csv(b"student_id,name\n\xff\xfe\x00,b", **LIMITS)

    assert excinfo.value.code == "UNSUPPORTED_ENCODING"


def test_file_larger_than_limit_is_refused(sample):
    with pytest.raises(ValidationInputError) as excinfo:
        parse_csv(sample("students_dirty.csv"), **{**LIMITS, "max_upload_bytes": 10})

    # Not "FILE_TOO_LARGE" — that string is the API's HTTP 413 code, and a job's
    # error_code must stay distinguishable from it.
    assert excinfo.value.code == "SIZE_LIMIT_EXCEEDED"


def test_row_limit_is_enforced():
    rows = ["student_id"] + [f"S{i:05d}" for i in range(10)]

    with pytest.raises(ValidationInputError) as excinfo:
        parse_csv(csv_bytes(*rows), **{**LIMITS, "max_rows": 5})

    assert excinfo.value.code == "ROW_LIMIT_EXCEEDED"


def test_column_limit_is_enforced():
    data = csv_bytes("a,b,c", "1,2,3")

    with pytest.raises(ValidationInputError) as excinfo:
        parse_csv(data, **{**LIMITS, "max_columns": 2})

    assert excinfo.value.code == "TOO_MANY_COLUMNS"


def test_quoted_commas_and_newlines_stay_in_one_record():
    data = b'student_id,name,age,department\nS001,"Asha, the\nfirst",20,COMP\n'
    parsed = parse_csv(data, **LIMITS)

    assert parsed.total_rows == 1
    assert parsed.rows[0][1] == "Asha, the\nfirst"


def test_headers_are_trimmed_before_comparison():
    data = csv_bytes(" student_id , name ", " S001 , Asha ")
    parsed = parse_csv(data, **LIMITS)

    assert parsed.headers == ("student_id", "name")
    # Values keep their original padding for export.
    assert parsed.rows[0] == (" S001 ", " Asha ")


# --------------------------------------------------------- value semantics


def test_leading_zeros_are_preserved(sample, demo_rules):
    """String parsing, not numeric: '007' must not become 7."""
    data = sample("students_leading_zeros.csv")
    parsed = parse_csv(data, **LIMITS)
    result = validate(parsed, demo_rules, error_preview_limit=100)

    assert parsed.rows[0][0] == "007"
    assert result.valid_rows == 2


def test_literal_na_is_a_value_not_a_blank(sample, demo_rules):
    """keep_default_na=False: 'NA' is data; only '' is missing."""
    result = run(sample("students_literal_na.csv"), demo_rules)

    # Record 1 has name 'NA' (valid); record 2 has a genuinely blank name.
    assert result.valid_mask[0] is True
    assert result.valid_mask[1] is False
    assert CODE_REQUIRED_VALUE in {e.code for e in result.row_errors[1]}


def test_whitespace_only_required_value_is_blank():
    rules = RuleSet.from_payload(
        {"required_columns": ["name"], "required_values": ["name"]}
    )
    result = run(csv_bytes("name", '"   "'), rules)

    assert result.valid_rows == 0
    assert CODE_REQUIRED_VALUE in {e.code for e in result.row_errors[0]}


def test_checks_use_trimmed_values_but_exports_keep_originals():
    rules = RuleSet.from_payload(
        {
            "required_columns": ["department"],
            "allowed_values": {"department": ["COMP"]},
        }
    )
    data = csv_bytes("department", "  COMP  ")
    parsed = parse_csv(data, **LIMITS)
    result = validate(parsed, rules, error_preview_limit=100)

    assert result.valid_rows == 1  # trimmed value matched
    assert parsed.rows[0][0] == "  COMP  "  # original preserved for export


def test_allowed_values_are_case_sensitive():
    rules = RuleSet.from_payload(
        {
            "required_columns": ["department"],
            "allowed_values": {"department": ["COMP"]},
        }
    )
    result = run(csv_bytes("department", "comp"), rules)

    assert result.valid_rows == 0
    assert CODE_ALLOWED_VALUE in {e.code for e in result.row_errors[0]}


def test_unique_comparison_is_case_sensitive():
    rules = RuleSet.from_payload(
        {"required_columns": ["student_id"], "unique_columns": ["student_id"]}
    )
    result = run(csv_bytes("student_id", "s001", "S001"), rules)

    assert result.valid_rows == 2  # different strings, so not duplicates


def test_unique_ignores_surrounding_whitespace():
    rules = RuleSet.from_payload(
        {"required_columns": ["student_id"], "unique_columns": ["student_id"]}
    )
    result = run(csv_bytes("student_id", "S001", " S001 "), rules)

    assert result.valid_rows == 0
    assert result.failure_counts[CODE_DUPLICATE_VALUE] == 2


# ------------------------------------------------------------ numeric edges


NUMERIC_RULES = RuleSet.from_payload(
    {
        "required_columns": ["age"],
        "numeric_ranges": {"age": {"min": 16, "max": 100, "integer": True}},
    }
)


@pytest.mark.parametrize(
    ("value", "expected_valid"),
    [
        ("16", True),  # inclusive lower bound
        ("100", True),  # inclusive upper bound
        ("58", True),
        ("15", False),
        ("101", False),
        ("20.5", False),  # integer required
        ("abc", False),  # not a number
        ('""', False),  # explicitly empty value
        ("1e400", False),  # overflows to infinity
        ("NaN", False),  # parses, but is not finite
        ("  20  ", True),  # trimmed before conversion
    ],
)
def test_numeric_range_boundaries(value, expected_valid):
    result = run(csv_bytes("age", value), NUMERIC_RULES)

    assert (result.valid_rows == 1) is expected_valid, value


def test_numeric_column_without_integer_requirement_accepts_fractions():
    rules = RuleSet.from_payload(
        {"required_columns": ["score"], "numeric_ranges": {"score": {"min": 0, "max": 10}}}
    )
    result = run(csv_bytes("score", "7.5"), rules)

    assert result.valid_rows == 1


def test_numeric_failure_message_distinguishes_causes():
    result = run(csv_bytes("age", "abc", "20.5", "15"), NUMERIC_RULES)

    messages = [errors[0].message for errors in result.row_errors]
    assert "is not a number" in messages[0]
    assert "whole number" in messages[1]
    assert "between 16 and 100" in messages[2]


# ---------------------------------------------------------------- reporting


def test_error_preview_is_capped():
    rules = RuleSet.from_payload(
        {"required_columns": ["name"], "required_values": ["name"]}
    )
    data = csv_bytes("name", *(['""'] * 25))  # 25 records with an empty name
    result = validate(parse_csv(data, **LIMITS), rules, error_preview_limit=10)

    assert len(result.error_preview) == 10
    assert result.rejected_rows == 25


def test_error_preview_record_numbers_are_one_based_and_skip_valid_rows(demo_rules, sample):
    result = run(sample("students_dirty.csv"), demo_rules)

    numbers = [entry["record_number"] for entry in result.error_preview]
    assert numbers == [2, 3, 4, 5]  # record 1 and 6 are the valid rows


def test_valid_percentage_rounds_to_two_places():
    rules = RuleSet.from_payload(
        {"required_columns": ["name"], "required_values": ["name"]}
    )
    data = csv_bytes("name", "Asha", "Bilal", '""')  # 2 of 3 valid
    result = run(data, rules)

    assert result.valid_percentage == 66.67


def test_blank_lines_are_skipped_not_treated_as_malformed():
    """A stray blank line is whitespace, not a wrong-width record."""
    data = b"student_id,name\n\nS001,Asha\n\nS002,Bilal\n\n"
    parsed = parse_csv(data, **LIMITS)

    assert parsed.total_rows == 2


def test_explicitly_quoted_empty_value_is_still_a_record():
    """'""' parses as [''] and must count as a row, unlike a blank line."""
    parsed = parse_csv(b'student_id,name\nS001,""\n', **LIMITS)

    assert parsed.total_rows == 1
    assert parsed.rows[0] == ("S001", "")


def test_wrong_width_is_still_malformed_after_blank_line_handling():
    with pytest.raises(ValidationInputError) as excinfo:
        parse_csv(b"a,b\n1,2\n3\n", **LIMITS)

    assert excinfo.value.code == "MALFORMED_CSV"
