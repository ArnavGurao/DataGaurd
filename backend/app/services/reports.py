"""Artifact generation: valid rows, rejected rows and the JSON report (README 12.2).

Exports deliberately preserve the original cell values byte for byte — no
stripping, no re-typing — because downstream tooling depends on them. That does
mean a value beginning ``=``, ``+``, ``-`` or ``@`` remains a live formula when
the file is opened in a spreadsheet; see ``docs/api-contract.md`` for the
documented policy. We never claim an escaped export is identical to the input.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Sequence

from .validator import ParsedCsv, ValidationResult

#: Bumped when the report shape changes so stored reports stay interpretable.
REPORT_SCHEMA_VERSION = "1.0"

RECORD_NUMBER_COLUMN = "__dg_record_number"
ERROR_CODES_COLUMN = "__dg_error_codes"
ERROR_MESSAGES_COLUMN = "__dg_error_messages"

REJECTED_ANNOTATION_COLUMNS: tuple[str, ...] = (
    RECORD_NUMBER_COLUMN,
    ERROR_CODES_COLUMN,
    ERROR_MESSAGES_COLUMN,
)

#: One rejected row can fail several rules, so these are joined, not counted.
_ERROR_SEPARATOR = "|"


def _encode(rows: Sequence[Sequence[Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def build_valid_csv(parsed: ParsedCsv, result: ValidationResult) -> bytes:
    """Original columns and original values for rows passing every rule.

    When nothing passes, this is still written with a header and zero data rows
    so the artifact set is predictable (README 12.2).
    """
    rows: list[Sequence[Any]] = [list(parsed.headers)]
    rows.extend(list(row) for row, ok in zip(parsed.rows, result.valid_mask) if ok)
    return _encode(rows)


def build_rejected_csv(parsed: ParsedCsv, result: ValidationResult) -> bytes:
    """Original columns plus the three ``__dg_`` annotation columns."""
    headers = list(parsed.headers) + list(REJECTED_ANNOTATION_COLUMNS)
    rows: list[Sequence[Any]] = [headers]

    for position, (row, errors) in enumerate(zip(parsed.rows, result.row_errors)):
        if not errors:
            continue
        rows.append(
            [
                *row,
                position + 1,  # logical record number, 1-based
                _ERROR_SEPARATOR.join(dict.fromkeys(error.code for error in errors)),
                _ERROR_SEPARATOR.join(error.message for error in errors),
            ]
        )
    return _encode(rows)


def build_report(
    *,
    job_id: str,
    rules: dict[str, Any],
    result: ValidationResult,
    processing_seconds: float,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """The report payload, before serialization."""
    generated_at = generated_at or datetime.now(timezone.utc)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "job_id": str(job_id),
        "generated_at": _iso(generated_at),
        "processing_seconds": round(processing_seconds, 3),
        "rules": rules,
        "counts": {
            "total_rows": result.total_rows,
            "valid_rows": result.valid_rows,
            "rejected_rows": result.rejected_rows,
            "valid_percentage": result.valid_percentage,
        },
        # Counts are per rule and per row: one row failing two rules counts in
        # both, so these need not sum to rejected_rows (README 8.2).
        "failure_counts": result.failure_counts,
        "dataset_errors": result.dataset_errors,
        "error_preview": result.error_preview,
        "error_preview_truncated": result.rejected_rows > len(result.error_preview),
    }


def build_report_json(report: dict[str, Any]) -> bytes:
    """Serialize the report, refusing NaN/Infinity as JSON numbers (README 12.2)."""
    return json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
