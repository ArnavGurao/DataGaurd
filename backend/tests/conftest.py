"""Shared fixtures for the backend test suite.

None of these tests need a database or AWS credentials: the validation engine
takes its limits as arguments, so the core behaviour is testable in isolation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from app.services.rules import RuleSet

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "samples"

#: Baseline limits from README 1.2, passed explicitly to keep tests independent
#: of whatever is in a developer's .env.
LIMITS = {
    "max_upload_bytes": 5 * 1024 * 1024,
    "max_rows": 20_000,
    "max_columns": 50,
}

#: The sample rule set from README 9.7.
DEMO_RULES_PAYLOAD = {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16, "max": 100, "integer": True}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]},
}


@pytest.fixture
def demo_rules() -> RuleSet:
    return RuleSet.from_payload(DEMO_RULES_PAYLOAD)


@pytest.fixture
def sample() -> Callable[[str], bytes]:
    """Read a fixture file from ``samples/`` as raw bytes."""

    def _load(name: str) -> bytes:
        return (SAMPLES_DIR / name).read_bytes()

    return _load


def csv_bytes(*lines: str) -> bytes:
    """Build CSV bytes from lines, always terminating each with a newline."""
    return ("\n".join(lines) + "\n").encode("utf-8")
