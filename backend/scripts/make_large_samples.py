"""Generate the oversized and too-many-rows fixtures on demand (README 11.4).

Two fixtures in section 11.4 cannot be committed: one is larger than the 5 MiB
upload limit and the other carries more than 20,000 data rows. Committing either
would bloat the repository that section 7 asks us to keep free of junk, so we
generate them from the configured limits instead and let
``samples/generated/.gitignore`` keep the results out of git.

Both files are deliberately well-formed: the demo header
``student_id,name,age,department`` followed by rows that would pass validation.
That is the point of the fixtures — the oversized file must be refused for its
size alone and the row-heavy file for its row count alone, so neither may trip a
second rule on the way to the behaviour under test.

Run from either location::

    python scripts/make_large_samples.py          # from backend/
    python backend/scripts/make_large_samples.py  # from the repo root
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path
from typing import NamedTuple, Sequence

# Paths are resolved from this file, not the working directory, so the script
# behaves identically however it is invoked (README 11.4).
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_OUT_DIR = REPO_ROOT / "samples" / "generated"

#: Demo schema shared with the committed samples in samples/.
HEADER: tuple[str, ...] = ("student_id", "name", "age", "department")
DEPARTMENTS: tuple[str, ...] = ("COMP", "IT", "EXTC")

# How far past the byte limit the oversized fixture runs. One row would already
# do, but a margin keeps the rejection unambiguous if the server measures the
# upload slightly differently (BOM handling, multipart framing).
OVERSIZE_MARGIN_BYTES = 64 * 1024


class Limits(NamedTuple):
    """The three baseline limits this script needs (README 1.2)."""

    max_upload_bytes: int
    max_csv_rows: int
    max_csv_columns: int


# Mirrors the documented baseline in app/config.py. Used only when that module
# cannot be imported, so a missing dependency never blocks fixture generation.
FALLBACK_LIMITS = Limits(max_upload_bytes=5 * 1024 * 1024, max_csv_rows=20_000, max_csv_columns=50)


def load_limits() -> Limits:
    """Read the limits from app.config, falling back to the documented defaults.

    ``app`` only imports when ``backend/`` is on the path, which the caller's
    working directory does not guarantee, so the import is made importable here
    and still guarded — an unimportable config is a warning, not a crash.
    """
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    try:
        from app.config import get_settings

        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - any failure means "use the defaults"
        print(
            f"warning: could not read app.config ({exc}); "
            f"using documented defaults: {FALLBACK_LIMITS.max_upload_bytes} bytes, "
            f"{FALLBACK_LIMITS.max_csv_rows} rows."
        )
        return FALLBACK_LIMITS

    return Limits(
        max_upload_bytes=settings.max_upload_bytes,
        max_csv_rows=settings.max_csv_rows,
        max_csv_columns=settings.max_csv_columns,
    )


def encode_row(row: Sequence[str]) -> bytes:
    """Encode one row with the csv module so quoting stays correct.

    Byte counts are accumulated from these encoded rows, which only works
    because the writer — not string concatenation — decides the field quoting.
    """
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerow(row)
    return buffer.getvalue().encode("utf-8")


def student_row(index: int, name_padding: int = 0) -> tuple[str, ...]:
    """One valid demo row; ``name_padding`` widens it without changing the schema."""
    name = f"Student {index:06d}"
    if name_padding:
        name = f"{name} {'x' * name_padding}"
    return (
        f"S{index:06d}",
        name,
        str(18 + index % 8),
        DEPARTMENTS[index % len(DEPARTMENTS)],
    )


def write_oversized(path: Path, limits: Limits) -> tuple[int, int]:
    """Write header plus enough padded rows to exceed the byte limit.

    Rows are padded so the byte limit is reached well before the row limit.
    Without padding a file could only be made oversized by breaking the row
    count, and it would then be refused for two reasons instead of the one this
    fixture exists to isolate.
    """
    header = encode_row(HEADER)
    # Aim each row at roughly the average size that fills the byte budget in
    # max_csv_rows rows, so the loop stops on bytes rather than on the row cap.
    target_row_bytes = limits.max_upload_bytes // limits.max_csv_rows + 64
    padding = max(0, target_row_bytes - len(encode_row(student_row(1))))

    written = len(header)
    rows = 0
    byte_target = limits.max_upload_bytes + OVERSIZE_MARGIN_BYTES
    with path.open("wb") as handle:
        handle.write(header)
        while written <= byte_target and rows < limits.max_csv_rows:
            rows += 1
            chunk = encode_row(student_row(rows, padding))
            handle.write(chunk)
            written += len(chunk)

    # If the row cap stopped us first the fixture is wrong, not merely small.
    if written <= limits.max_upload_bytes:
        raise RuntimeError(
            f"{path.name} reached {rows:,} rows (the limit) at only {written:,} bytes; "
            "increase the padding so size can be tested on its own."
        )
    return written, rows


def write_too_many_rows(path: Path, limits: Limits) -> tuple[int, int]:
    """Write header plus one row past the configured maximum, kept small on purpose.

    Only the row count should fail here, so the unpadded rows keep the file far
    below the byte limit.
    """
    data_rows = limits.max_csv_rows + 1
    written = 0
    with path.open("wb") as handle:
        for row in (HEADER, *(student_row(index) for index in range(1, data_rows + 1))):
            chunk = encode_row(row)
            handle.write(chunk)
            written += len(chunk)

    if written > limits.max_upload_bytes:
        raise RuntimeError(
            f"{path.name} is {written:,} bytes, above the {limits.max_upload_bytes:,} byte limit; "
            "the row count would not be the only reason it is refused."
        )
    return written, data_rows


def write_gitignore(directory: Path) -> Path:
    """Ignore everything here except this file (README 7).

    ``*`` matches ``.gitignore`` itself, so the negation is what keeps the rule
    present in a fresh clone; without it the directory would look untracked and
    the next commit could add the fixtures.
    """
    path = directory / ".gitignore"
    path.write_text("*\n!.gitignore\n", encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the oversized and too-many-rows CSV fixtures (README 11.4).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"directory to write the fixtures into (default: {DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args(argv)

    limits = load_limits()
    if len(HEADER) > limits.max_csv_columns:
        raise RuntimeError(
            f"the demo schema has {len(HEADER)} columns, above the {limits.max_csv_columns} limit."
        )

    out_dir: Path = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_gitignore(out_dir)

    oversized = out_dir / "students_oversized.csv"
    size, rows = write_oversized(oversized, limits)
    print(f"{oversized}")
    print(f"  bytes:     {size:,} (upload limit {limits.max_upload_bytes:,}) -> 413 expected")
    print(f"  data rows: {rows:,} (row limit {limits.max_csv_rows:,}, kept within it)")

    too_many = out_dir / "students_too_many_rows.csv"
    size, rows = write_too_many_rows(too_many, limits)
    print(f"{too_many}")
    print(f"  bytes:     {size:,} (upload limit {limits.max_upload_bytes:,}, kept below it)")
    print(f"  data rows: {rows:,} (row limit {limits.max_csv_rows:,}) -> ROW_LIMIT_EXCEEDED expected")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
