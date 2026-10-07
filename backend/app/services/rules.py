"""Rule-set parsing and server-side rule validation (README 9.7, 11.2).

Rules are validated once at creation and stored as JSON. A frozen copy is
snapshotted onto each job so that later edits to a rule set cannot retroactively
change the meaning of a finished audit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

#: Output annotations are namespaced so a source column can never collide with
#: one of our generated columns (README 11.1, 12.2).
RESERVED_COLUMN_PREFIX = "__dg_"

#: Stable rule codes. Reports and the frontend key off these values.
CODE_REQUIRED_VALUE = "REQUIRED_VALUE"
CODE_DUPLICATE_VALUE = "DUPLICATE_VALUE"
CODE_NUMERIC_RANGE = "NUMERIC_RANGE"
CODE_ALLOWED_VALUE = "ALLOWED_VALUE"
CODE_MISSING_COLUMN = "MISSING_COLUMN"


class RuleSetError(ValueError):
    """A structurally invalid rule payload. Surfaces as HTTP 422."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class NumericRange:
    """Inclusive numeric bounds for one column."""

    min: float | None = None
    max: float | None = None
    integer: bool = False

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.min is not None:
            payload["min"] = self.min
        if self.max is not None:
            payload["max"] = self.max
        if self.integer:
            payload["integer"] = True
        return payload


@dataclass(frozen=True)
class RuleSet:
    """A validated, immutable rule set."""

    required_columns: tuple[str, ...] = ()
    required_values: tuple[str, ...] = ()
    unique_columns: tuple[str, ...] = ()
    numeric_ranges: Mapping[str, NumericRange] = None  # type: ignore[assignment]
    allowed_values: Mapping[str, tuple[str, ...]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.numeric_ranges is None:
            object.__setattr__(self, "numeric_ranges", {})
        if self.allowed_values is None:
            object.__setattr__(self, "allowed_values", {})

    @property
    def referenced_columns(self) -> set[str]:
        """Every column any rule mentions, required columns aside."""
        return (
            set(self.required_values)
            | set(self.unique_columns)
            | set(self.numeric_ranges)
            | set(self.allowed_values)
        )

    def to_json(self) -> dict[str, Any]:
        """Canonical form stored in ``rule_sets.rules_json`` and job snapshots."""
        return {
            "required_columns": list(self.required_columns),
            "required_values": list(self.required_values),
            "unique_columns": list(self.unique_columns),
            "numeric_ranges": {c: r.to_json() for c, r in self.numeric_ranges.items()},
            "allowed_values": {c: list(v) for c, v in self.allowed_values.items()},
        }

    @classmethod
    def from_payload(cls, payload: Any) -> "RuleSet":
        """Validate and normalize a rule payload from the API.

        Every referenced column must also be a required column: a rule that can
        never run because its column may be absent is a configuration mistake,
        not a runtime concern.
        """
        if not isinstance(payload, Mapping):
            raise RuleSetError("INVALID_RULES", "Rules must be a JSON object.")
        if not payload:
            raise RuleSetError("EMPTY_RULES", "At least one rule is required.")

        known = {
            "required_columns",
            "required_values",
            "unique_columns",
            "numeric_ranges",
            "allowed_values",
        }
        unknown = set(payload) - known
        if unknown:
            raise RuleSetError(
                "UNSUPPORTED_RULE",
                "Unsupported rule keys: " + ", ".join(sorted(str(k) for k in unknown)) + ".",
            )

        required_columns = _column_list(payload.get("required_columns"), "required_columns")
        if not required_columns:
            raise RuleSetError("EMPTY_RULES", "required_columns must name at least one column.")

        required_set = set(required_columns)

        def _checked(name: str) -> tuple[str, ...]:
            columns = _column_list(payload.get(name), name)
            for column in columns:
                if column not in required_set:
                    raise RuleSetError(
                        "COLUMN_NOT_REQUIRED",
                        f"Column '{column}' in {name} must also appear in required_columns.",
                    )
            return columns

        required_values = _checked("required_values")
        unique_columns = _checked("unique_columns")

        numeric_ranges = _numeric_ranges(payload.get("numeric_ranges"), required_set)
        allowed_values = _allowed_values(payload.get("allowed_values"), required_set)

        rules = cls(
            required_columns=required_columns,
            required_values=required_values,
            unique_columns=unique_columns,
            numeric_ranges=numeric_ranges,
            allowed_values=allowed_values,
        )
        return rules

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "RuleSet":
        """Rebuild from a stored snapshot. Assumes it was validated on write."""
        return cls(
            required_columns=tuple(payload.get("required_columns", ())),
            required_values=tuple(payload.get("required_values", ())),
            unique_columns=tuple(payload.get("unique_columns", ())),
            numeric_ranges={
                column: NumericRange(
                    min=spec.get("min"),
                    max=spec.get("max"),
                    integer=bool(spec.get("integer", False)),
                )
                for column, spec in (payload.get("numeric_ranges") or {}).items()
            },
            allowed_values={
                column: tuple(values)
                for column, values in (payload.get("allowed_values") or {}).items()
            },
        )


def _column_list(raw: Any, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise RuleSetError("INVALID_RULES", f"{field} must be a list of column names.")

    columns: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise RuleSetError("INVALID_RULES", f"{field} entries must be strings.")
        column = item.strip()
        if not column:
            raise RuleSetError("INVALID_COLUMN_NAME", f"{field} contains a blank column name.")
        if column.startswith(RESERVED_COLUMN_PREFIX):
            raise RuleSetError(
                "RESERVED_COLUMN",
                f"Column '{column}' uses the reserved '{RESERVED_COLUMN_PREFIX}' prefix.",
            )
        if column in seen:
            continue
        seen.add(column)
        columns.append(column)
    return tuple(columns)


def _numeric_ranges(raw: Any, required_set: set[str]) -> dict[str, NumericRange]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise RuleSetError("INVALID_NUMERIC_RANGE", "numeric_ranges must be an object.")

    ranges: dict[str, NumericRange] = {}
    for column, spec in raw.items():
        column = str(column).strip()
        if column not in required_set:
            raise RuleSetError(
                "COLUMN_NOT_REQUIRED",
                f"Column '{column}' in numeric_ranges must also appear in required_columns.",
            )
        if not isinstance(spec, Mapping):
            raise RuleSetError(
                "INVALID_NUMERIC_RANGE", f"numeric_ranges['{column}'] must be an object."
            )

        unknown = set(spec) - {"min", "max", "integer"}
        if unknown:
            raise RuleSetError(
                "UNSUPPORTED_RULE",
                f"numeric_ranges['{column}'] has unsupported keys: "
                + ", ".join(sorted(str(k) for k in unknown))
                + ".",
            )

        minimum = _bound(spec.get("min"), column, "min")
        maximum = _bound(spec.get("max"), column, "max")
        if minimum is None and maximum is None:
            raise RuleSetError(
                "INVALID_NUMERIC_RANGE",
                f"numeric_ranges['{column}'] needs a min, a max, or both.",
            )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise RuleSetError(
                "INVALID_NUMERIC_RANGE",
                f"numeric_ranges['{column}'] has min greater than max.",
            )

        integer = spec.get("integer", False)
        if not isinstance(integer, bool):
            raise RuleSetError(
                "INVALID_NUMERIC_RANGE", f"numeric_ranges['{column}'].integer must be true or false."
            )

        ranges[column] = NumericRange(min=minimum, max=maximum, integer=integer)
    return ranges


def _bound(raw: Any, column: str, name: str) -> float | None:
    if raw is None:
        return None
    # bool is an int subclass; True as a bound is always a mistake.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise RuleSetError(
            "INVALID_NUMERIC_RANGE", f"numeric_ranges['{column}'].{name} must be a number."
        )
    # NaN and infinity must be refused here rather than accepted. JSON permits
    # the bare literals NaN/Infinity and 1e400 overflows to inf, so they can
    # arrive over the wire. Accepting them is the worst outcome: every
    # comparison against NaN is False, which turns the range check into a
    # silent no-op, and the bound is then unusable downstream — psycopg emits
    # it as the bare token `NaN`, which jsonb rejects, and
    # build_report_json's allow_nan=False raises on it (README 12.2, 9.7).
    if not math.isfinite(raw):
        raise RuleSetError(
            "INVALID_NUMERIC_RANGE",
            f"numeric_ranges['{column}'].{name} must be a finite number.",
        )
    return float(raw)


def _allowed_values(raw: Any, required_set: set[str]) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise RuleSetError("INVALID_ALLOWED_VALUES", "allowed_values must be an object.")

    allowed: dict[str, tuple[str, ...]] = {}
    for column, values in raw.items():
        column = str(column).strip()
        if column not in required_set:
            raise RuleSetError(
                "COLUMN_NOT_REQUIRED",
                f"Column '{column}' in allowed_values must also appear in required_columns.",
            )
        if not isinstance(values, (list, tuple)) or not values:
            raise RuleSetError(
                "INVALID_ALLOWED_VALUES",
                f"allowed_values['{column}'] must be a non-empty list.",
            )

        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise RuleSetError(
                    "INVALID_ALLOWED_VALUES",
                    f"allowed_values['{column}'] entries must be strings.",
                )
            value = value.strip()
            if not value:
                raise RuleSetError(
                    "INVALID_ALLOWED_VALUES",
                    f"allowed_values['{column}'] contains a blank value.",
                )
            cleaned.append(value)

        # Comparison is case-sensitive in the baseline (README 11.1).
        allowed[column] = tuple(dict.fromkeys(cleaned))
    return allowed
