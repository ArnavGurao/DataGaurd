"""Rule payload validation (README 9.7)."""

from __future__ import annotations

import pytest

from app.services.rules import RuleSet, RuleSetError
from conftest import DEMO_RULES_PAYLOAD


def test_demo_payload_is_accepted_and_normalized():
    rules = RuleSet.from_payload(DEMO_RULES_PAYLOAD)

    assert rules.required_columns == ("student_id", "name", "age", "department")
    assert rules.numeric_ranges["age"].min == 16
    assert rules.numeric_ranges["age"].integer is True
    assert rules.allowed_values["department"] == ("COMP", "IT", "EXTC")


def test_round_trip_through_json_is_stable():
    rules = RuleSet.from_payload(DEMO_RULES_PAYLOAD)
    restored = RuleSet.from_json(rules.to_json())

    assert restored.to_json() == rules.to_json()


def test_a_job_snapshot_is_independent_of_later_edits():
    """from_json must not alias the stored payload (rules freeze at submission)."""
    stored = RuleSet.from_payload(DEMO_RULES_PAYLOAD).to_json()
    snapshot = RuleSet.from_json(stored)

    stored["required_columns"].append("injected")

    assert "injected" not in snapshot.required_columns


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({}, "EMPTY_RULES"),
        ({"required_columns": []}, "EMPTY_RULES"),
        ({"required_columns": ["a"], "unknown_rule": []}, "UNSUPPORTED_RULE"),
        ({"required_columns": ["a"], "required_values": ["b"]}, "COLUMN_NOT_REQUIRED"),
        ({"required_columns": ["a"], "unique_columns": ["b"]}, "COLUMN_NOT_REQUIRED"),
        ({"required_columns": ["a"], "allowed_values": {"b": ["x"]}}, "COLUMN_NOT_REQUIRED"),
        ({"required_columns": ["a"], "numeric_ranges": {"b": {"min": 1}}}, "COLUMN_NOT_REQUIRED"),
        ({"required_columns": ["__dg_out"]}, "RESERVED_COLUMN"),
        ({"required_columns": ["  "]}, "INVALID_COLUMN_NAME"),
        ({"required_columns": ["a"], "numeric_ranges": {"a": {"min": 10, "max": 1}}}, "INVALID_NUMERIC_RANGE"),
        ({"required_columns": ["a"], "numeric_ranges": {"a": {}}}, "INVALID_NUMERIC_RANGE"),
        ({"required_columns": ["a"], "numeric_ranges": {"a": {"min": "0"}}}, "INVALID_NUMERIC_RANGE"),
        ({"required_columns": ["a"], "numeric_ranges": {"a": {"min": True}}}, "INVALID_NUMERIC_RANGE"),
        ({"required_columns": ["a"], "numeric_ranges": {"a": {"min": 0, "integer": "yes"}}}, "INVALID_NUMERIC_RANGE"),
        ({"required_columns": ["a"], "allowed_values": {"a": []}}, "INVALID_ALLOWED_VALUES"),
        ({"required_columns": ["a"], "allowed_values": {"a": [1]}}, "INVALID_ALLOWED_VALUES"),
        ({"required_columns": ["a"], "allowed_values": {"a": ["  "]}}, "INVALID_ALLOWED_VALUES"),
        ({"required_columns": "a"}, "INVALID_RULES"),
        ({"required_columns": [1]}, "INVALID_RULES"),
        ("not an object", "INVALID_RULES"),
    ],
)
def test_invalid_payloads_are_rejected_with_a_stable_code(payload, code):
    with pytest.raises(RuleSetError) as excinfo:
        RuleSet.from_payload(payload)

    assert excinfo.value.code == code


def test_min_equal_to_max_is_allowed():
    rules = RuleSet.from_payload(
        {"required_columns": ["age"], "numeric_ranges": {"age": {"min": 21, "max": 21}}}
    )

    assert rules.numeric_ranges["age"].min == rules.numeric_ranges["age"].max == 21


def test_min_only_and_max_only_are_allowed():
    rules = RuleSet.from_payload(
        {
            "required_columns": ["a", "b"],
            "numeric_ranges": {"a": {"min": 0}, "b": {"max": 10}},
        }
    )

    assert rules.numeric_ranges["a"].max is None
    assert rules.numeric_ranges["b"].min is None


def test_repeated_columns_are_deduplicated_not_rejected():
    rules = RuleSet.from_payload(
        {"required_columns": ["a", "a", "b"], "required_values": ["a", "a"]}
    )

    assert rules.required_columns == ("a", "b")
    assert rules.required_values == ("a",)


def test_column_names_are_trimmed():
    rules = RuleSet.from_payload(
        {"required_columns": [" student_id "], "unique_columns": ["student_id"]}
    )

    assert rules.required_columns == ("student_id",)
    assert rules.unique_columns == ("student_id",)


def test_referenced_columns_covers_every_rule_kind():
    rules = RuleSet.from_payload(DEMO_RULES_PAYLOAD)

    assert rules.referenced_columns == {"student_id", "name", "age", "department"}


@pytest.mark.parametrize("bound", [float("nan"), float("inf"), float("-inf"), 1e400])
def test_non_finite_bounds_are_refused(bound):
    """A NaN or infinite bound must be rejected, never accepted.

    NaN is the dangerous one: every comparison against it is False, so the range
    check would silently stop rejecting anything at all. Infinity is also
    unusable downstream — psycopg serializes it as a bare ``Infinity`` token,
    which PostgreSQL's jsonb refuses, and the report writer sets
    ``allow_nan=False``.
    """
    with pytest.raises(RuleSetError) as excinfo:
        RuleSet.from_payload(
            {
                "required_columns": ["age"],
                "numeric_ranges": {"age": {"min": bound, "max": 100, "integer": True}},
            }
        )

    assert excinfo.value.code == "INVALID_NUMERIC_RANGE"
    assert "finite" in str(excinfo.value)


@pytest.mark.parametrize("key", ["min", "max"])
def test_the_max_bound_is_checked_too(key):
    with pytest.raises(RuleSetError) as excinfo:
        RuleSet.from_payload(
            {"required_columns": ["age"], "numeric_ranges": {"age": {key: float("nan")}}}
        )

    assert excinfo.value.code == "INVALID_NUMERIC_RANGE"


def test_ordinary_fractional_bounds_are_still_accepted():
    """Finiteness is the only new restriction; ordinary floats keep working."""
    rules = RuleSet.from_payload(
        {"required_columns": ["score"], "numeric_ranges": {"score": {"min": 0.5, "max": 9.5}}}
    )

    assert rules.numeric_ranges["score"].min == 0.5
    assert rules.numeric_ranges["score"].integer is False
