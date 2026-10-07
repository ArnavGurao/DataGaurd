"""Deterministic CSV validation engine (README 11).

Two rules govern everything here:

* values are read as **strings** and never coerced on the way in, so leading
  zeros survive and a literal ``NA`` stays ``NA`` rather than becoming blank;
* checks run against **trimmed** values while exports keep the original ones.

Input faults (malformed quoting, an oversized file) raise
``ValidationInputError`` and fail the job. Bad *data* never does: a file whose
rows all fail its rules is a successful audit with a poor score, not a
processing failure (README 11.2, 13.1).
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .rules import (
    CODE_ALLOWED_VALUE,
    CODE_DUPLICATE_VALUE,
    CODE_MISSING_COLUMN,
    CODE_NUMERIC_RANGE,
    CODE_REQUIRED_VALUE,
    RESERVED_COLUMN_PREFIX,
    NumericRange,
    RuleSet,
)

#: Report sections and failure counts keep this order regardless of input order.
ALL_RULE_CODES: tuple[str, ...] = (
    CODE_REQUIRED_VALUE,
    CODE_DUPLICATE_VALUE,
    CODE_NUMERIC_RANGE,
    CODE_ALLOWED_VALUE,
    CODE_MISSING_COLUMN,
)


class ValidationInputError(Exception):
    """The uploaded bytes cannot be audited at all. Maps to a FAILED job."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedCsv:
    """Header names and data records exactly as they appeared in the file."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    @property
    def total_rows(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class RowError:
    code: str
    column: str | None
    message: str

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "column": self.column, "message": self.message}


@dataclass
class ValidationResult:
    total_rows: int
    valid_rows: int
    rejected_rows: int
    valid_percentage: float
    failure_counts: dict[str, int]
    dataset_errors: list[dict[str, Any]]
    error_preview: list[dict[str, Any]]
    #: Parallel to the parsed rows; used to build the export files.
    row_errors: list[list[RowError]]
    valid_mask: list[bool]

    @property
    def has_rejects(self) -> bool:
        return self.rejected_rows > 0


def parse_csv(
    data: bytes,
    *,
    max_upload_bytes: int,
    max_rows: int,
    max_columns: int,
) -> ParsedCsv:
    """Decode and structurally validate an uploaded CSV.

    Python's ``csv.reader`` runs before Pandas so header problems surface on the
    real header row rather than after Pandas has renamed duplicates
    (README 11.1 step 3), and so a wrong field count is an error instead of a
    silently padded row.
    """
    if not data:
        raise ValidationInputError("EMPTY_DATASET", "The uploaded file is empty.")
    if len(data) > max_upload_bytes:
        # Deliberately NOT "FILE_TOO_LARGE": that string is the API's HTTP 413
        # code (README 9.7). This is the worker-side limit, which surfaces as a
        # failed job's error_code, and it is named to match its sibling
        # ROW_LIMIT_EXCEEDED (README 11.4) so a client branching on the code can
        # always tell the two layers apart.
        raise ValidationInputError(
            "SIZE_LIMIT_EXCEEDED",
            f"Upload a CSV of {max_upload_bytes // (1024 * 1024)} MiB or less.",
        )

    try:
        # utf-8-sig transparently strips a BOM when present (README 1.2).
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationInputError(
            "UNSUPPORTED_ENCODING", "The file must be UTF-8 encoded (with or without a BOM)."
        ) from exc

    # A wholly blank line yields an empty record. RFC 4180 puts no data there
    # and a trailing blank line is common, so these are dropped. An explicitly
    # quoted empty value ('""') parses as [''] and is NOT blank — it stays a
    # real record whose value happens to be empty. A line holding only spaces
    # is also NOT blank: that is a one-field record whose value is whitespace,
    # so it is kept and width-checked like any other row. Do not "tidy" this
    # into a whitespace strip — that would silently discard data.
    records = [record for record in _read_records(text) if record]
    if not records:
        raise ValidationInputError("EMPTY_DATASET", "The uploaded file has no header row.")

    headers = _validate_headers(records[0], max_columns=max_columns)
    body = records[1:]

    if not body:
        raise ValidationInputError(
            "EMPTY_DATASET", "The file has a header row but no data rows."
        )
    if len(body) > max_rows:
        raise ValidationInputError(
            "ROW_LIMIT_EXCEEDED",
            f"The file has {len(body)} data rows; the limit is {max_rows}.",
        )

    width = len(headers)
    for index, record in enumerate(body, start=1):
        if len(record) != width:
            raise ValidationInputError(
                "MALFORMED_CSV",
                f"Record {index} has {len(record)} fields but the header has {width}. "
                "Check for stray commas or broken quoting.",
            )

    return ParsedCsv(headers=headers, rows=tuple(tuple(record) for record in body))


def inspect_headers(data: bytes, *, max_columns: int) -> tuple[str, ...]:
    """Upload-time check of the header row only.

    The upload handler rejects a structurally impossible file immediately, while
    row-level validation stays in the worker (README 9.7). This reads just the
    first two records, so it costs nothing on a large file.
    """
    if not data:
        raise ValidationInputError("EMPTY_DATASET", "The uploaded file is empty.")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationInputError(
            "UNSUPPORTED_ENCODING", "The file must be UTF-8 encoded (with or without a BOM)."
        ) from exc

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        # Skip blank lines so the header is the first record that carries data.
        header: list[str] | None = None
        has_data = False
        for record in reader:
            if not record:
                continue
            if header is None:
                header = record
                continue
            has_data = True
            break
    except csv.Error as exc:
        raise ValidationInputError("MALFORMED_CSV", f"The CSV is malformed: {exc}.") from exc

    if header is None:
        raise ValidationInputError("EMPTY_DATASET", "The uploaded file has no header row.")
    if not has_data:
        raise ValidationInputError("EMPTY_DATASET", "The file has a header row but no data rows.")

    return _validate_headers(header, max_columns=max_columns)


def _read_records(text: str) -> list[list[str]]:
    """Read records with the stdlib parser, preserving quoted commas/newlines."""
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        return [record for record in reader]
    except csv.Error as exc:
        raise ValidationInputError("MALFORMED_CSV", f"The CSV is malformed: {exc}.") from exc


def _validate_headers(raw_headers: Sequence[str], *, max_columns: int) -> tuple[str, ...]:
    headers: list[str] = []
    seen: set[str] = set()

    for raw in raw_headers:
        # utf-8-sig strips a BOM only at offset 0, so a stray U+FEFF reaches us
        # as part of the first header — e.g. after a blank line, or when the
        # BOM sits between records. Left in place it silently renames the
        # column ('﻿id' != 'id'), which turns a configured rule into
        # MISSING_COLUMN and skips it entirely. A zero-width no-break space is
        # never part of an intended column name.
        header = raw.lstrip("﻿").strip()
        if not header:
            raise ValidationInputError("INVALID_HEADER", "Every column needs a non-blank header.")
        if header.startswith(RESERVED_COLUMN_PREFIX):
            raise ValidationInputError(
                "RESERVED_HEADER",
                f"Column '{header}' uses the reserved '{RESERVED_COLUMN_PREFIX}' prefix.",
            )
        if header in seen:
            raise ValidationInputError(
                "DUPLICATE_HEADERS", f"Column '{header}' appears more than once."
            )
        seen.add(header)
        headers.append(header)

    if len(headers) > max_columns:
        raise ValidationInputError(
            "TOO_MANY_COLUMNS",
            f"The file has {len(headers)} columns; the limit is {max_columns}.",
        )
    return tuple(headers)


def validate(
    parsed: ParsedCsv,
    rules: RuleSet,
    *,
    error_preview_limit: int = 100,
) -> ValidationResult:
    """Evaluate every applicable rule and collect all errors per row."""
    total = parsed.total_rows
    row_errors: list[list[RowError]] = [[] for _ in range(total)]
    dataset_errors: list[dict[str, Any]] = []

    missing = [column for column in rules.required_columns if column not in parsed.headers]
    if missing:
        # A missing column is a schema failure inside a *completed* audit: every
        # row is rejected and each one carries the reason (README 11.2).
        for column in missing:
            message = f"Required column '{column}' is missing from the file."
            dataset_errors.append(
                {"code": CODE_MISSING_COLUMN, "column": column, "message": message}
            )
        for errors in row_errors:
            for column in missing:
                errors.append(
                    RowError(
                        CODE_MISSING_COLUMN,
                        column,
                        f"Required column '{column}' is missing from the file.",
                    )
                )
        return _summarize(
            parsed,
            row_errors,
            dataset_errors=dataset_errors,
            error_preview_limit=error_preview_limit,
        )

    # Checks use trimmed values; the parsed rows keep the originals for export.
    trimmed = pd.DataFrame(
        {column: [row[index].strip() for row in parsed.rows] for index, column in enumerate(parsed.headers)}
    )

    _check_required_values(trimmed, rules, row_errors)
    _check_unique_values(trimmed, rules, row_errors)
    _check_numeric_ranges(trimmed, rules, row_errors)
    _check_allowed_values(trimmed, rules, row_errors)

    return _summarize(
        parsed, row_errors, dataset_errors=dataset_errors, error_preview_limit=error_preview_limit
    )


def _check_required_values(
    trimmed: pd.DataFrame, rules: RuleSet, row_errors: list[list[RowError]]
) -> None:
    for column in rules.required_values:
        blanks = (trimmed[column] == "").to_numpy()
        for position in np.flatnonzero(blanks):
            row_errors[int(position)].append(
                RowError(CODE_REQUIRED_VALUE, column, f"'{column}' must not be blank.")
            )


def _check_unique_values(
    trimmed: pd.DataFrame, rules: RuleSet, row_errors: list[list[RowError]]
) -> None:
    for column in rules.unique_columns:
        values = trimmed[column]
        # keep=False flags every occurrence, so both sides of a duplicate pair
        # are rejected rather than the first being kept (README 11.2).
        duplicated = values.duplicated(keep=False).to_numpy()
        for position in np.flatnonzero(duplicated):
            position = int(position)
            value = values.iloc[position]
            row_errors[position].append(
                RowError(
                    CODE_DUPLICATE_VALUE,
                    column,
                    f"'{column}' value '{value}' appears more than once in this file.",
                )
            )


def _check_numeric_ranges(
    trimmed: pd.DataFrame, rules: RuleSet, row_errors: list[list[RowError]]
) -> None:
    for column, spec in rules.numeric_ranges.items():
        raw = trimmed[column]
        numeric = pd.to_numeric(raw, errors="coerce").to_numpy(dtype="float64")
        finite = np.isfinite(numeric)

        flagged = ~finite  # conversion failures and non-finite values alike
        if spec.min is not None:
            flagged |= finite & (numeric < spec.min)
        if spec.max is not None:
            flagged |= finite & (numeric > spec.max)
        if spec.integer:
            # np.mod on an infinite value raises a RuntimeWarning; the result is
            # masked out by `finite` anyway, so silence the noise rather than let
            # it look like a real problem in the logs.
            with np.errstate(invalid="ignore"):
                fractional = np.mod(numeric, 1)
            flagged |= finite & (fractional != 0)

        expectation = _range_description(spec)
        for position in np.flatnonzero(flagged):
            position = int(position)
            value = raw.iloc[position]
            value_float = numeric[position]

            if not math.isfinite(value_float):
                detail = f"'{value}' is not a number."
            elif spec.integer and value_float != int(value_float):
                detail = f"'{value}' must be a whole number."
            else:
                detail = f"'{value}' must be {expectation}."

            row_errors[position].append(
                RowError(CODE_NUMERIC_RANGE, column, f"'{column}' {detail}")
            )


def _check_allowed_values(
    trimmed: pd.DataFrame, rules: RuleSet, row_errors: list[list[RowError]]
) -> None:
    for column, allowed in rules.allowed_values.items():
        values = trimmed[column]
        unexpected = (~values.isin(allowed)).to_numpy()
        permitted = ", ".join(allowed)
        for position in np.flatnonzero(unexpected):
            position = int(position)
            row_errors[position].append(
                RowError(
                    CODE_ALLOWED_VALUE,
                    column,
                    f"'{column}' value '{values.iloc[position]}' is not one of: {permitted}.",
                )
            )


def _range_description(spec: NumericRange) -> str:
    if spec.min is not None and spec.max is not None:
        phrase = f"between {_number(spec.min)} and {_number(spec.max)} inclusive"
    elif spec.min is not None:
        phrase = f"at least {_number(spec.min)}"
    else:
        phrase = f"at most {_number(spec.max)}"
    return phrase + (" and a whole number" if spec.integer else "")


def _number(value: float) -> str:
    """Render 16.0 as '16' in messages; the client sends integers commonly."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _summarize(
    parsed: ParsedCsv,
    row_errors: list[list[RowError]],
    *,
    dataset_errors: list[dict[str, Any]],
    error_preview_limit: int,
) -> ValidationResult:
    total = parsed.total_rows
    valid_mask = [not errors for errors in row_errors]
    valid_rows = sum(valid_mask)
    rejected_rows = total - valid_rows

    failure_counts = {code: 0 for code in ALL_RULE_CODES}
    for errors in row_errors:
        for code in {error.code for error in errors}:
            failure_counts[code] = failure_counts.get(code, 0) + 1

    preview: list[dict[str, Any]] = []
    for position, errors in enumerate(row_errors):
        if not errors:
            continue
        if len(preview) >= error_preview_limit:
            break
        # Record number counts logical records, not physical lines: a quoted
        # field may span several lines (README 12.2).
        #
        # ``values`` is what makes the preview useful on its own: the rejected
        # row's original cells, keyed by column, so a reader can see the bad
        # value next to the reason. Without it the preview carries only an
        # opaque record number. Original (untrimmed) values are used, matching
        # the rejected export.
        preview.append(
            {
                "record_number": position + 1,
                "values": dict(zip(parsed.headers, parsed.rows[position])),
                "errors": [error.to_json() for error in errors],
            }
        )

    return ValidationResult(
        total_rows=total,
        valid_rows=valid_rows,
        rejected_rows=rejected_rows,
        valid_percentage=round(valid_rows / total * 100, 2) if total else 0.0,
        failure_counts=failure_counts,
        dataset_errors=dataset_errors,
        error_preview=preview,
        row_errors=row_errors,
        valid_mask=valid_mask,
    )
