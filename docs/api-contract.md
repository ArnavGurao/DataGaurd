# DataGuard API Contract

**Owner: B (backend).** Consumers: A (frontend), C (cloud/integration).
Sources: README 9.6, 9.7, 10, 12.2, 12.3, 13.1, and the implemented code in `backend/app/`.
Where the code and the README differ, the code wins and the difference is listed in §9.

## 0. Conventions

| Item | Rule |
|---|---|
| Base path | `/api` on every route; the browser always calls the same origin it served the page from (Vite proxy locally, Nginx in the cloud) |
| Body format | JSON, except `POST /api/jobs` (multipart/form-data) and `GET /api/downloads/{token}` (binary) |
| Field names | `snake_case`, exactly as the Pydantic models serialize them. Never `jobId` |
| Identifiers | UUID v4 strings |
| Timestamps | UTC, ISO 8601 with `Z` (e.g. `2026-09-30T08:00:00Z`) |
| Authentication | `Authorization: Bearer <access_token>` on protected routes |
| Token lifetime | 30 minutes (`JWT_EXPIRE_MINUTES=30`). Held in React memory only; reload means re-login |
| Ownership | A resource belonging to another user returns **404**, never 403 — its existence is not disclosed |
| Errors | One envelope, §2. **Every** failure — including 500 and every 503 — uses it. No route has an out-of-shape error body |

Limits enforced server-side (README 1.2, `app/config.py` defaults): 5 MiB upload, 20,000 data rows, 50 columns, UTF-8 / UTF-8-with-BOM, comma-separated, one header row. **Which layer enforces what decides where you see the error:** size, column count, encoding and header structure are checked at upload (so `POST /api/jobs` answers `413`/`422` and queues no job), while the 20,000-row limit is worker-only (`ROW_LIMIT_EXCEEDED` on a `FAILED` job) because knowing the row count means reading the whole file. See §2 for the full code table.

### Endpoint index

| Method and path | Auth | Owner | Success |
|---|---|---|---|
| `GET /api/health` | none | B | 200 |
| `GET /api/ready` | none | B | 200 / 503 |
| `POST /api/auth/register` | none | B | 201 |
| `POST /api/auth/login` | none | B | 200 |
| `GET /api/auth/me` | bearer | B | 200 |
| `POST /api/rule-sets` | bearer | B | 201 |
| `GET /api/rule-sets` | bearer | B | 200 |
| `POST /api/jobs` | bearer | B/C | 202 |
| `GET /api/jobs` | bearer | B | 200 |
| `GET /api/jobs/{job_id}` | bearer | B | 200 |
| `GET /api/jobs/{job_id}/report` | bearer | B | 200 |
| `GET /api/jobs/{job_id}/downloads/{kind}` | bearer | B/C | 200 |
| `GET /api/downloads/{token}` | none (signed token) | B/C | 200 |

---

## 1. Endpoints

### 1.1 `GET /api/health`

Liveness only. Answers even when the database is down.

| | |
|---|---|
| Auth | none |
| Owner | B |
| Success | `200` |

```json
{"status": "ok", "service": "dataguard-api"}
```

Errors: none. (A wrong method on this path gives `405 METHOD_NOT_ALLOWED`.)

### 1.2 `GET /api/ready`

Readiness. Runs `SELECT 1` against PostgreSQL. Reveals nothing about *why* it failed.

| | |
|---|---|
| Auth | none |
| Owner | B |
| Success | `200` usable / `503` unavailable |

```json
{"status": "ok", "database": "ok"}
```

The 200 is a success response and stays bare — it is not wrapped in an `error`
object.

Failure `503` uses the standard envelope, like every other failure:

```json
{"error": {"code": "STORAGE_UNAVAILABLE", "message": "The database is unavailable. Try again shortly."}}
```

> The code is `STORAGE_UNAVAILABLE` because README §9.7 maps 503 to "dependency
> unavailable" as a single status; the message names the database. Branch on the
> HTTP status, not on this code, if you only need to know "not ready".

### 1.3 `POST /api/auth/register`

| | |
|---|---|
| Auth | none |
| Owner | B |
| Success | `201` |

Request:

```json
{"email": "asha.rao@example.com", "password": "correct-horse-battery"}
```

Password must be 8–128 characters. Email is trimmed, lowercased, and must match `^[^@\s]+@[^@\s]+\.[^@\s]+$` within 320 characters. Uniqueness is enforced on the normalized form.

Success `201`:

```json
{"id": "8f14e45f-ea0b-4c0e-9b1a-2f3c4d5e6a7b", "email": "asha.rao@example.com"}
```

| Status | Code | Message |
|---|---|---|
| 409 | `CONFLICT` | `An account with that email already exists.` |
| 422 | `VALIDATION_ERROR` | `Invalid request. email: Value error, Enter a valid email address.` |
| 422 | `VALIDATION_ERROR` | `Invalid request. password: Value error, Password must be at least 8 characters.` |

A duplicate email that loses a race to the unique index reports the same 409.

### 1.4 `POST /api/auth/login`

| | |
|---|---|
| Auth | none |
| Owner | B |
| Success | `200` |

Request:

```json
{"email": "asha.rao@example.com", "password": "correct-horse-battery"}
```

Success `200`:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI4ZjE0ZTQ1Zi1lYTBiLTRjMGUtOWIxYS0yZjNjNGQ1ZTZhN2IiLCJ0b2tlbl91c2UiOiJhY2Nlc3MiLCJpYXQiOjE3NTkyMDMyMDAsImV4cCI6MTc1OTIwNTAwMH0.s3cr3t-s1gn4tur3",
  "token_type": "bearer"
}
```

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | `Incorrect email or password.` |
| 422 | `VALIDATION_ERROR` | `Invalid request. email: Value error, Enter a valid email address.` |

Unknown-email and wrong-password share one message on purpose, so the endpoint cannot enumerate accounts.

### 1.5 `GET /api/auth/me`

| | |
|---|---|
| Auth | bearer |
| Owner | B |
| Success | `200` |

```json
{"id": "8f14e45f-ea0b-4c0e-9b1a-2f3c4d5e6a7b", "email": "asha.rao@example.com"}
```

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | `Authentication required.` (no `Authorization` header) |
| 401 | `UNAUTHORIZED` | `Your session expired. Sign in again.` (expired `exp`) |
| 401 | `UNAUTHORIZED` | `Invalid authentication token.` (bad signature, wrong `token_use`, malformed `sub`, or the account no longer exists) |

### 1.6 `POST /api/rule-sets`

Path is `/api/rule-sets` with no trailing slash. Rules are validated on write and stored normalized; the job snapshot is taken from that stored form.

| | |
|---|---|
| Auth | bearer |
| Owner | B |
| Success | `201` |

Request (README 9.7 "Student Records"):

```json
{
  "name": "Student Records",
  "rules": {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16, "max": 100, "integer": true}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]}
  }
}
```

Success `201` — note the normalized form (all five keys always present, bounds echoed as floats):

```json
{
  "id": "b7c1a2d3-4e5f-4061-8a9b-0c1d2e3f4a5b",
  "name": "Student Records",
  "rules": {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16.0, "max": 100.0, "integer": true}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]}
  },
  "created_at": "2026-09-30T07:58:11Z"
}
```

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | as in §1.5 |
| 409 | `CONFLICT` | `You already have a rule set with that name.` |
| 422 | `VALIDATION_ERROR` | `Invalid request. name: Value error, Rule-set name must not be blank.` / `Invalid request. name: String should have at most 200 characters` / `Invalid request. rules: Input should be a valid dictionary` |
| 422 | rule-set code | any of `EMPTY_RULES`, `UNSUPPORTED_RULE`, `COLUMN_NOT_REQUIRED`, `INVALID_COLUMN_NAME`, `RESERVED_COLUMN`, `INVALID_NUMERIC_RANGE`, `INVALID_ALLOWED_VALUES`, `INVALID_RULES` — see §6 |

Rule-set names are unique per owner (`UNIQUE(owner_id, name)`).

### 1.7 `GET /api/rule-sets`

| | |
|---|---|
| Auth | bearer |
| Owner | B |
| Success | `200` |

Returns a **bare JSON array** (no envelope), newest first:

```json
[
  {
    "id": "b7c1a2d3-4e5f-4061-8a9b-0c1d2e3f4a5b",
    "name": "Student Records",
    "rules": {
      "required_columns": ["student_id", "name", "age", "department"],
      "required_values": ["student_id", "name", "age", "department"],
      "unique_columns": ["student_id"],
      "numeric_ranges": {"age": {"min": 16.0, "max": 100.0, "integer": true}},
      "allowed_values": {"department": ["COMP", "IT", "EXTC"]}
    },
    "created_at": "2026-09-30T07:58:11Z"
  }
]
```

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | as in §1.5 |

Only the caller's own rule sets are ever returned.

### 1.8 `POST /api/jobs`

Multipart upload. Cheap, certain checks only (ownership, size, extension, header sanity); row validation is the worker's job, so a dirty file never delays the response.

| | |
|---|---|
| Auth | bearer |
| Owner | B/C |
| Success | `202` |

**Exact form field names** (all three required):

| Field | Type | Notes |
|---|---|---|
| `title` | text | Trimmed; leading/trailing whitespace stripped; required, non-blank; stored truncated to 200 chars |
| `rule_set_id` | text | UUID of a rule set **owned by the caller** |
| `file` | file | Filename must end `.csv` (case-insensitive) |

Do not set `Content-Type` manually for FormData — the browser adds the multipart boundary.

Success `202`:

```json
{"job_id": "3f2b1c9e-7a4d-4e21-9b6f-0c8d5a1e2f34", "status": "PENDING_DISPATCH"}
```

The response does not wait for validation. Poll §1.10 for progress.

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | as in §1.5 |
| 404 | `NOT_FOUND` | `Rule set not found.` (missing, or owned by someone else) |
| 413 | `FILE_TOO_LARGE` | `Upload a CSV of 5 MiB or less.` (checked mid-stream; nothing is stored) |
| 422 | `VALIDATION_ERROR` | `A title is required.` |
| 422 | `VALIDATION_ERROR` | `Upload a .csv file.` |
| 422 | `VALIDATION_ERROR` | `Invalid request. file: Field required` (or `title`, `rule_set_id` missing; or `rule_set_id` is not a UUID) |
| 422 | header-check code | `EMPTY_DATASET`, `UNSUPPORTED_ENCODING`, `MALFORMED_CSV`, `INVALID_HEADER`, `RESERVED_HEADER`, `DUPLICATE_HEADERS`, `TOO_MANY_COLUMNS` — see §2 |
| 503 | `STORAGE_UNAVAILABLE` | `Storage is unavailable. Try again shortly.` (no job is created; nothing to clean up) |
| 500 | `INTERNAL_ERROR` | `The server could not complete the request.` |

Count limits are **not** checked here: a file with more than 20,000 data rows is accepted with 202 and later ends `FAILED` with `ROW_LIMIT_EXCEEDED` (README 11.4).

### 1.9 `GET /api/jobs?limit=20&offset=0`

| | |
|---|---|
| Auth | bearer |
| Owner | B |
| Success | `200` |

`limit` 1–100 (default 20), `offset` ≥ 0 (default 0). Newest first.

```json
{
  "items": [
    {
      "job_id": "3f2b1c9e-7a4d-4e21-9b6f-0c8d5a1e2f34",
      "title": "Student records audit",
      "status": "COMPLETED",
      "created_at": "2026-09-30T08:00:00Z",
      "started_at": "2026-09-30T08:00:02Z",
      "completed_at": "2026-09-30T08:00:05Z",
      "summary": {
        "total_rows": 6,
        "valid_rows": 2,
        "rejected_rows": 4,
        "valid_percentage": 33.33
      },
      "available_downloads": ["original", "valid", "rejected", "report"],
      "error": null
    },
    {
      "job_id": "a1d4f7c2-9b3e-4a58-8d21-6e0f9c4b7a12",
      "title": "Broken quoting check",
      "status": "FAILED",
      "created_at": "2026-09-30T07:40:00Z",
      "started_at": "2026-09-30T07:40:01Z",
      "completed_at": "2026-09-30T07:40:02Z",
      "summary": null,
      "available_downloads": ["original"],
      "error": {
        "code": "MALFORMED_CSV",
        "message": "The CSV is malformed: unexpected end of data.",
        "failure_kind": "INPUT"
      }
    }
  ],
  "total": 2,
  "limit": 20,
  "offset": 0
}
```

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | as in §1.5 |
| 422 | `VALIDATION_ERROR` | `Invalid request. limit: Input should be less than or equal to 100` (or `offset` < 0) |

### 1.10 `GET /api/jobs/{job_id}`

The polling endpoint. `{job_id}` must be a UUID.

| | |
|---|---|
| Auth | bearer |
| Owner | B |
| Success | `200` |

```json
{
  "job_id": "3f2b1c9e-7a4d-4e21-9b6f-0c8d5a1e2f34",
  "title": "Student records audit",
  "status": "COMPLETED",
  "created_at": "2026-09-30T08:00:00Z",
  "started_at": "2026-09-30T08:00:02Z",
  "completed_at": "2026-09-30T08:00:05Z",
  "summary": {
    "total_rows": 6,
    "valid_rows": 2,
    "rejected_rows": 4,
    "valid_percentage": 33.33
  },
  "available_downloads": ["original", "valid", "rejected", "report"],
  "error": null
}
```

While active, `summary` is `null`, `completed_at` is `null`, `error` is `null`, and `available_downloads` is `["original"]`.

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | as in §1.5 |
| 404 | `NOT_FOUND` | `Job not found.` (no such job, **or** it belongs to another user) |
| 422 | `VALIDATION_ERROR` | `Invalid request. job_id: Input should be a valid UUID` |

### 1.11 `GET /api/jobs/{job_id}/report`

Returns the stored `report.json` verbatim (see §7 for its shape).

| | |
|---|---|
| Auth | bearer |
| Owner | B |
| Success | `200` |

```json
{
  "schema_version": "1.0",
  "job_id": "3f2b1c9e-7a4d-4e21-9b6f-0c8d5a1e2f34",
  "generated_at": "2026-09-30T08:00:05Z",
  "processing_seconds": 0.412,
  "rules": {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16.0, "max": 100.0, "integer": true}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]}
  },
  "counts": {
    "total_rows": 6,
    "valid_rows": 2,
    "rejected_rows": 4,
    "valid_percentage": 33.33
  },
  "failure_counts": {
    "REQUIRED_VALUE": 1,
    "DUPLICATE_VALUE": 2,
    "NUMERIC_RANGE": 1,
    "ALLOWED_VALUE": 1,
    "MISSING_COLUMN": 0
  },
  "dataset_errors": [],
  "error_preview": [
    {"record_number": 2, "errors": [{"code": "REQUIRED_VALUE", "column": "name", "message": "'name' must not be blank."}]},
    {"record_number": 3, "errors": [{"code": "NUMERIC_RANGE", "column": "age", "message": "'age' '15' must be between 16 and 100 inclusive and a whole number."}]},
    {"record_number": 4, "errors": [{"code": "DUPLICATE_VALUE", "column": "student_id", "message": "'student_id' value 'S003' appears more than once in this file."}]},
    {"record_number": 5, "errors": [{"code": "ALLOWED_VALUE", "column": "department", "message": "'department' value 'MECH' is not one of: COMP, IT, EXTC."}]}
  ],
  "error_preview_truncated": false
}
```

The preview above is abridged: row 3 also carries a `DUPLICATE_VALUE` error (S003), which is why `DUPLICATE_VALUE` is 2. The preview holds at most 100 rejected rows; `error_preview_truncated` says whether anything was cut.

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | as in §1.5 |
| 404 | `NOT_FOUND` | `Job not found.` / `The report is no longer available.` (row says COMPLETED but the object is gone) |
| 409 | `REPORT_NOT_READY` | `The report is not ready yet.` (job is not `COMPLETED`) |
| 422 | `VALIDATION_ERROR` | `Invalid request. job_id: Input should be a valid UUID` |

409 rather than 404 for a pending job: the report is expected to exist later. A `FAILED` job also returns 409 — it will never produce a report.

### 1.12 `GET /api/jobs/{job_id}/downloads/{kind}`

Issues a short-lived URL for one artifact, after an ownership check. Never returns file bytes.

| | |
|---|---|
| Auth | bearer |
| Owner | B/C |
| Success | `200` |

`kind` ∈ `original` \| `valid` \| `rejected` \| `report`.

Local mode:

```json
{
  "url": "/api/downloads/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI4ZjE0ZTQ1Zi1lYTBiLTRjMGUtOWIxYS0yZjNjNGQ1ZTZhN2IiLCJqb2JfaWQiOiIzZjJiMWM5ZS03YTRkLTRlMjEtOWI2Zi0wYzhkNWExZTJmMzQiLCJraW5kIjoicmVqZWN0ZWQiLCJ0b2tlbl91c2UiOiJkb3dubG9hZCIsImlhdCI6MTc1OTIwNDAwNSwiZXhwIjoxNzU5MjA0MDY1fQ.9f8a7b6c5d4e3f2a1b0c9d8e",
  "filename": "students_dirty_rejected.csv",
  "expires_in": 60
}
```

AWS mode is the same shape with an absolute presigned S3 URL:

```json
{
  "url": "https://dataguard-artifacts.s3.us-east-1.amazonaws.com/outputs/8f14e45f-.../rejected.csv?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=60&X-Amz-Signature=...",
  "filename": "students_dirty_rejected.csv",
  "expires_in": 60
}
```

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | as in §1.5 |
| 404 | `NOT_FOUND` | `Unknown artifact 'valid2'.` (kind not one of the four) |
| 404 | `NOT_FOUND` | `Job not found.` |
| 404 | `NOT_FOUND` | `That artifact is not available for this job.` (e.g. `valid` before completion) |
| 422 | `VALIDATION_ERROR` | `Invalid request. job_id: Input should be a valid UUID` |

`expires_in` is seconds and comes from `DOWNLOAD_URL_TTL_SECONDS` (default 60) — the same value between local and AWS mode.

### 1.13 `GET /api/downloads/{token}`

Local-mode download only. The browser reaches this by navigating to the URL from §1.12, so **no `Authorization` header is sent** — the signed token is the capability. The token identifies one artifact of one job and is re-checked against the database on every request.

| | |
|---|---|
| Auth | none; the signed token is the authorization |
| Owner | B/C |
| Success | `200` binary |

Success `200`: file bytes.

```
Content-Type: text/csv
Content-Disposition: attachment; filename="students_dirty_rejected.csv"; filename*=UTF-8''students_dirty_rejected.csv
```

`report` is served as `application/json`; every other kind as `text/csv`.

| Status | Code | Message |
|---|---|---|
| 401 | `UNAUTHORIZED` | `Your session expired. Sign in again.` (token past `exp`) |
| 401 | `UNAUTHORIZED` | `Invalid authentication token.` (bad signature or wrong token kind) |
| 401 | `UNAUTHORIZED` | `Invalid download token.` (well-signed but malformed claims) |
| 404 | `NOT_FOUND` | `Not found.` (server is in S3 mode; the job is gone; or the owner no longer matches) |
| 404 | `NOT_FOUND` | `Unknown artifact.` |
| 404 | `NOT_FOUND` | `That artifact is not available for this job.` |
| 404 | `NOT_FOUND` | `That artifact is no longer available.` (metadata exists, object missing) |

Never log this URL or put it in a screenshot: it is a temporary bearer capability. Expiry does not delete the object.

---

## 2. Error shape

Every deliberate error, every request-validation failure, and every unexpected crash returns exactly this envelope:

```json
{"error": {"code": "FILE_TOO_LARGE", "message": "Upload a CSV of 5 MiB or less."}}
```

`code` is a stable identifier the UI may branch on; `message` is a full sentence meant to be shown to a human. FastAPI's default 422 body is replaced, so the frontend parses one shape everywhere.

Reading it in the API helper:

```javascript
const data = await response.json().catch(() => ({}));
if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status})`);
```

### Application error codes (`backend/app/errors.py`)

| Code | HTTP | Fires when |
|---|---|---|
| `VALIDATION_ERROR` | 422 (default for 400) | Pydantic rejects the request body/path/query; blank upload title; non-`.csv` filename |
| `UNAUTHORIZED` | 401 (default for 403) | Missing, expired, malformed, or wrong-purpose token; wrong login credentials; invalid download token |
| `NOT_FOUND` | 404 | Missing job/rule set/artifact — including any resource owned by another user |
| `CONFLICT` | 409 (default for 409) | Duplicate email on register; duplicate rule-set name for one owner |
| `REPORT_NOT_READY` | 409 | `/report` requested while the job is not `COMPLETED` |
| `FILE_TOO_LARGE` | 413 (default for 413) | Upload body exceeds `MAX_UPLOAD_BYTES` (5 MiB) |
| `INTERNAL_ERROR` | 500 | Unhandled exception; also the default code for any status not in the mapping |
| `STORAGE_UNAVAILABLE` | 503 (default for 503) | Writing the original failed; worker-side storage failures surface as the job's `error.code` |
| `METHOD_NOT_ALLOWED` | 405 | Wrong HTTP method on an existing path |
| `RULE_SET_INVALID` | — | **Defined but never emitted.** The rule-set router surfaces the specific `RuleSetError` code instead (§6). Branch on §6's codes, not this one |

### Rule-set validation codes (`backend/app/services/rules.py`)

All reach the client as HTTP **422**, with the code passed through unchanged.

| Code | Fires when |
|---|---|
| `INVALID_RULES` | `rules` is not a JSON object; a column-list field is not a list; a list entry is not a string |
| `EMPTY_RULES` | The `rules` object is empty; `required_columns` is missing or empty |
| `UNSUPPORTED_RULE` | An unknown top-level key, or an unknown key inside one `numeric_ranges` entry |
| `COLUMN_NOT_REQUIRED` | A column referenced by `required_values`, `unique_columns`, `numeric_ranges`, or `allowed_values` is absent from `required_columns` |
| `INVALID_COLUMN_NAME` | A column-list field contains a blank or whitespace-only name |
| `RESERVED_COLUMN` | A column name begins with the reserved prefix `__dg_` |
| `INVALID_NUMERIC_RANGE` | `numeric_ranges` is not an object; an entry is not an object; an entry has neither `min` nor `max`; `min > max`; a bound is not a number (or is a boolean); `integer` is not a boolean |
| `INVALID_ALLOWED_VALUES` | `allowed_values` is not an object; an entry is not a non-empty list; an entry is not a string; an entry is blank after trimming |

### CSV input codes (`backend/app/services/validator.py`)

These appear at upload as `422` (header inspection only) and/or as the `error.code` of a `FAILED` job (worker parsing).

| Code | At upload | On a FAILED job | Fires when |
|---|---|---|---|
| `EMPTY_DATASET` | 422 | `failure_kind=INPUT` | No bytes; no header row; header but zero data rows |
| `UNSUPPORTED_ENCODING` | 422 | `failure_kind=INPUT` | Bytes are not UTF-8 / UTF-8-with-BOM |
| `MALFORMED_CSV` | 422 | `failure_kind=INPUT` | Broken quoting or any other parser error |
| `INVALID_HEADER` | 422 | `failure_kind=INPUT` | A header is blank after trimming |
| `RESERVED_HEADER` | 422 | `failure_kind=INPUT` | A source header begins with `__dg_` |
| `DUPLICATE_HEADERS` | 422 | `failure_kind=INPUT` | A header repeats after trimming |
| `TOO_MANY_COLUMNS` | 422 | `failure_kind=INPUT` | More than 50 columns |
| `ROW_LIMIT_EXCEEDED` | not checked | `failure_kind=INPUT` | More than 20,000 data rows (worker only) |
| `SIZE_LIMIT_EXCEEDED` | not checked | `failure_kind=INPUT` | Body over 5 MiB (worker only — the upload path refuses it earlier with `413 FILE_TOO_LARGE`) |
| `STORAGE_UNAVAILABLE` | `503 STORAGE_UNAVAILABLE` | `failure_kind=RETRY_EXHAUSTED` after 3 attempts | Storage read/write failed while processing |
| `FILE_TOO_LARGE` | `413 FILE_TOO_LARGE` | — | Upload exceeds the size limit |

### Per-row rule codes (report and `rejected.csv` only — never HTTP statuses)

`REQUIRED_VALUE`, `DUPLICATE_VALUE`, `NUMERIC_RANGE`, `ALLOWED_VALUE`, `MISSING_COLUMN`. `MISSING_COLUMN` is both a dataset-level error and a per-row annotation on every row of that completed-but-broken audit.

### Status mapping

| Status | Meaning | Typical code |
|---|---|---|
| 200 / 201 / 202 | Success (202 = accepted for background processing) | — |
| 400 | Generic bad request | `VALIDATION_ERROR` |
| 401 | Invalid or expired token; failed login | `UNAUTHORIZED` |
| 404 | Missing **or unauthorized** resource | `NOT_FOUND` |
| 405 | Wrong method | `METHOD_NOT_ALLOWED` |
| 409 | Conflict or not-ready-yet | `CONFLICT`, `REPORT_NOT_READY` |
| 413 | Upload over the size limit | `FILE_TOO_LARGE` |
| 422 | Invalid request, invalid rules, or failed early CSV checks | `VALIDATION_ERROR` or a specific rule-set/CSV code |
| 500 | Unexpected server error | `INTERNAL_ERROR` |
| 503 | Dependency unavailable (storage, database) | `STORAGE_UNAVAILABLE` |

---

## 3. Status model

`job.status` is always one of five strings (`JobStatus` in `backend/app/models.py`).

| Status | Kind | Meaning |
|---|---|---|
| `PENDING_DISPATCH` | active (poll) | Original stored and job committed; the publisher has not yet sent it to the queue |
| `QUEUED` | active (poll) | Dispatched and waiting for a worker to claim it |
| `RUNNING` | active (poll) | A worker holds a lease and is validating |
| `COMPLETED` | terminal (stop) | The audit ran and a report exists |
| `FAILED` | terminal (stop) | The audit could not finish |

`PENDING_DISPATCH → QUEUED → RUNNING → COMPLETED` is the normal path. `PENDING_DISPATCH → RUNNING` is legal when the worker consumes the message before the status update lands. `RUNNING → QUEUED` is a retryable failure being re-queued; `RUNNING → FAILED` is a permanent error or retry exhaustion.

### COMPLETED vs FAILED

This distinction is the one thing not to get wrong in the UI:

- **`COMPLETED`** means the audit *ran*. It may contain many invalid rows. A file where every row is rejected, or where a required column is missing entirely, is still `COMPLETED` — bad data is a result, not a failure. `summary` is populated and `error` is `null`.
- **`FAILED`** means the audit *could not finish* — malformed CSV, unsupported encoding, row limit, or storage unavailable after retries. `summary` is `null` and `error` is populated:

```json
{
  "code": "ROW_LIMIT_EXCEEDED",
  "message": "The file has 24000 data rows; the limit is 20000.",
  "failure_kind": "INPUT"
}
```

`failure_kind` is `INPUT` (bad input, safe to acknowledge), `RETRY_EXHAUSTED` (three attempts, moved to the DLQ), or `INTERNAL`.

A `FAILED` job still exposes `available_downloads: ["original"]` and a `409 REPORT_NOT_READY` on `/report`. Do not show a chart for it; show the error message.

---

## 4. Download flow

Downloads are a three-step indirection because the browser must fetch bytes from a URL it can navigate to directly, without an `Authorization` header.

```
1. GET /api/jobs/{job_id}/downloads/{kind}      Authorization: Bearer <access_token>
   -> 200 {"url": "...", "filename": "...", "expires_in": 60}
2. window.location.assign(result.url)
3. Browser fetches `url` with no Authorization header:
     local mode -> GET /api/downloads/{signed token}  (this API verifies it again)
     AWS mode   -> GET <presigned S3 URL>             (S3 verifies the signature)
```

Frontend usage:

```javascript
const result = await apiRequest(`/jobs/${jobId}/downloads/rejected`, { token });
window.location.assign(result.url);
```

The four `kind` values and when each is available:

| `kind` | Filename suffix | Available from | Content |
|---|---|---|---|
| `original` | `_original.csv` | Immediately — from the moment the job is accepted | The uploaded file, byte for byte |
| `valid` | `_valid.csv` | After `COMPLETED` | Header + rows passing every rule (header only if all rows failed) |
| `rejected` | `_rejected.csv` | After `COMPLETED` | Header + rejected rows + three `__dg_` annotation columns (§7) |
| `report` | `_report.json` | After `COMPLETED` | The JSON report (§1.11) |

The `filename` is derived from the sanitized original name: `students_dirty.csv` + `rejected` → `students_dirty_rejected.csv`.

`available_downloads` on the job object is the authoritative list for the UI — render only those buttons. Requesting an unavailable kind returns `404 NOT_FOUND`.

Mode differences, same response shape:

| Mode | `url` | Lifetime |
|---|---|---|
| Local (`STORAGE_MODE=local`) | Relative path `/api/downloads/{signed JWT}` on this API | `expires_in` seconds (default 60) |
| AWS (`STORAGE_MODE=s3`) | Absolute presigned S3 GET URL with an attachment filename | 60 seconds (same `DOWNLOAD_URL_TTL_SECONDS`) |

Both are temporary bearer capabilities: do not log them, screenshot them, or cache them. An expired URL does not delete the object — request a fresh one. In S3 mode `GET /api/downloads/{token}` returns `404 not found` by design; downloads never flow back through the API.

---

## 5. Polling guidance

| Rule | Value |
|---|---|
| Interval | Every **3 seconds** |
| Poll while | `status` is `PENDING_DISPATCH`, `QUEUED`, or `RUNNING` |
| Stop on | `COMPLETED` or `FAILED` (terminal), component unmount, or an auth/resource error |
| Loop shape | Request-then-timeout — schedule the next fetch *after* the previous response, never a bare `setInterval` (overlapping requests) |
| Cancellation | `AbortController` on unmount |
| Errors to act on | `401 UNAUTHORIZED` → clear the in-memory session and show login; `404 NOT_FOUND` → the job is gone or not yours, stop and show a message |
| Errors to ignore | A single transient network/5xx failure — keep polling; do not create a second job |

```javascript
useEffect(() => {
  const controller = new AbortController();
  let timer;
  const ACTIVE = ['PENDING_DISPATCH', 'QUEUED', 'RUNNING'];

  async function tick() {
    const job = await apiRequest(`/jobs/${jobId}`, { token, signal: controller.signal });
    setJob(job);
    if (ACTIVE.includes(job.status)) timer = setTimeout(tick, 3000);
  }
  tick().catch(() => {});
  return () => { clearTimeout(timer); controller.abort(); };
}, [jobId, token]);
```

The upload response (`202`, `PENDING_DISPATCH`) is the first state; begin polling from there. Refreshing the page requires a fresh login (the token lives in memory), then resume polling from `/api/jobs`.

---

## 6. Rule payload reference

The `rules` object accepts exactly five keys. Anything else is rejected — there is no pass-through.

| Key | Type | Meaning |
|---|---|---|
| `required_columns` | list of strings | Columns that must exist in the header. At least one is required. Every other rule's column must appear here |
| `required_values` | list of strings | Columns whose trimmed value must not be empty |
| `unique_columns` | list of strings | Columns whose trimmed values must be unique within the file. **Every** occurrence of a duplicated value is rejected |
| `numeric_ranges` | object: column → `{min?, max?, integer?}` | Inclusive bounds. At least one of `min`/`max` is required. `integer: true` additionally rejects fractional values |
| `allowed_values` | object: column → non-empty list of strings | Case-sensitive membership test on the trimmed value |

Normalization applied on write (and therefore visible in the response and in every job snapshot):

- Column names are trimmed; duplicates within a list are dropped, first occurrence kept.
- `required_columns`, `required_values`, `unique_columns`, `allowed_values`, and `numeric_ranges` keys are always present in the stored form, even when empty.
- `numeric_ranges` bounds are stored and echoed as floats: `16` becomes `16.0`.
- `allowed_values` entries are trimmed and de-duplicated; comparison is case-sensitive.

### Validation rejections

| Condition | Code | Message |
|---|---|---|
| `rules` is empty | `EMPTY_RULES` | `At least one rule is required.` |
| `required_columns` missing or `[]` | `EMPTY_RULES` | `required_columns must name at least one column.` |
| Unsupported top-level key | `UNSUPPORTED_RULE` | `Unsupported rule keys: min_values.` |
| Unsupported key inside a range | `UNSUPPORTED_RULE` | `numeric_ranges['age'] has unsupported keys: strict.` |
| Rule column not in `required_columns` | `COLUMN_NOT_REQUIRED` | `Column 'age' in numeric_ranges must also appear in required_columns.` |
| Blank column name in a list | `INVALID_COLUMN_NAME` | `unique_columns contains a blank column name.` |
| Column starts with `__dg_` | `RESERVED_COLUMN` | `Column '__dg_record_number' uses the reserved '__dg_' prefix.` |
| `required_columns` is a string, not a list | `INVALID_RULES` | `required_columns must be a list of column names.` |
| Non-string list entry | `INVALID_RULES` | `required_columns entries must be strings.` |
| `numeric_ranges["age"]` not an object | `INVALID_NUMERIC_RANGE` | `numeric_ranges['age'] must be an object.` |
| Neither `min` nor `max` | `INVALID_NUMERIC_RANGE` | `numeric_ranges['age'] needs a min, a max, or both.` |
| `min > max` | `INVALID_NUMERIC_RANGE` | `numeric_ranges['age'] has min greater than max.` |
| Bound is not a number (incl. `true`) | `INVALID_NUMERIC_RANGE` | `numeric_ranges['age'].min must be a number.` |
| `integer` not a boolean | `INVALID_NUMERIC_RANGE` | `numeric_ranges['age'].integer must be true or false.` |
| `allowed_values["department"]` not a non-empty list | `INVALID_ALLOWED_VALUES` | `allowed_values['department'] must be a non-empty list.` |
| Non-string allowed value | `INVALID_ALLOWED_VALUES` | `allowed_values['department'] entries must be strings.` |
| Blank allowed value | `INVALID_ALLOWED_VALUES` | `allowed_values['department'] contains a blank value.` |

All of the above return HTTP **422** with the code in `error.code`.

### Worked example — README 9.7 "Student Records"

Request:

```json
{
  "name": "Student Records",
  "rules": {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16, "max": 100, "integer": true}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]}
  }
}
```

Response `201`:

```json
{
  "id": "b7c1a2d3-4e5f-4061-8a9b-0c1d2e3f4a5b",
  "name": "Student Records",
  "rules": {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16.0, "max": 100.0, "integer": true}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]}
  },
  "created_at": "2026-09-30T07:58:11Z"
}
```

Applied to `samples/students_dirty.csv` this yields 6 total rows, 2 valid, 4 rejected, 33.33% valid, and failure counts `REQUIRED_VALUE: 1`, `DUPLICATE_VALUE: 2`, `NUMERIC_RANGE: 1`, `ALLOWED_VALUE: 1`, `MISSING_COLUMN: 0`. Row 3 (S003, age 15) fails twice, which is why the counts sum to 5, not 4.

---

## 7. Export policy

### `valid.csv`

Original columns, original values, rows that passed every rule. Written even when zero rows pass (header only). Never re-typed or re-formatted.

### `rejected.csv`

The original columns, in the original order, plus exactly three appended annotation columns:

| Column | Contents |
|---|---|
| `__dg_record_number` | **1-based logical data-record number**, not the physical file line. Record 1 is the first data row after the header. Quoted fields may contain embedded newlines, so the line number and the record number diverge |
| `__dg_error_codes` | The rule codes for that row, joined with `|`, de-duplicated, in evaluation order: `REQUIRED_VALUE` → `DUPLICATE_VALUE` → `NUMERIC_RANGE` → `ALLOWED_VALUE` → `MISSING_COLUMN`. Example: `DUPLICATE_VALUE|NUMERIC_RANGE` |
| `__dg_error_messages` | The human-readable messages for that row, joined with `|`, same order. Example: `'student_id' value 'S003' appears more than once in this file.|'age' '15' must be between 16 and 100 inclusive and a whole number.` (the numeric message quotes the column and the value without the word "value"; the duplicate and allowed-value messages do include it) |

Both columns use `|` because a row can fail several rules; a single row's error list is never split across rows. Written even when zero rows fail (header only). Source headers beginning `__dg_` are rejected at upload, so these names cannot collide with an input column.

### Spreadsheet-formula caveat

Exports preserve raw cell values byte for byte — no stripping, no prefixing, no re-typing. That is what makes them machine-readable, and it has a consequence:

> A value beginning with `=`, `+`, `-`, or `@` remains a **live formula** when the CSV is opened in a spreadsheet application. We do not escape it in `valid.csv` or `rejected.csv`, and we do **not** claim any escaped export is byte-identical to the input.

If a spreadsheet-safe variant is ever needed it must be a separately named, separately labelled artifact — never a silent change to these files. Note that `-` is common in legitimate data (negative numbers, dates), so blanket escaping is not harmless.

### `report.json`

Schema `1.0`. Always generated on `COMPLETED`, including a completed audit that rejected every row. `NaN` and `Infinity` are never emitted as JSON numbers — a non-numeric value fails a rule rather than leaking into the report.

---

## 8. Counting and rounding

| Field | Meaning |
|---|---|
| `total_rows` | Data records in the file (header excluded) |
| `valid_rows` | Records failing zero rules |
| `rejected_rows` | `total_rows - valid_rows` |
| `valid_percentage` | `round(valid_rows / total_rows * 100, 2)` — **rounded to 2 decimal places**. `0.0` when there are no rows |
| `failure_counts` | Per rule, the number of **distinct rows** that tripped that rule |
| `error_preview` | At most 100 rejected records, each with its `record_number` and full error list |
| `error_preview_truncated` | `true` when `rejected_rows` exceeds the preview |

**`failure_counts` do not sum to `rejected_rows`.** A row failing two rules is counted once in each rule's total but only once in `rejected_rows`, so the per-rule sum is greater than or equal to the rejected count. On the dirty sample: 5 failure counts across 4 rejected rows. Label the UI accordingly — "failures by rule" is not "rejected rows by rule".

The same rounding and the same non-summation apply to `summary` on the job object and to `counts` in `report.json`.

---

## 9. Code vs README — known differences

| # | Difference | Impact |
|---|---|---|
| 1 | `RULE_SET_INVALID` is defined in `errors.py` but never emitted; the rule-set router passes the specific `RuleSetError` code through instead | Frontend must branch on the §6 codes; `RULE_SET_INVALID` will never appear |
| 2 | A worker-side oversize failure reports `error.code = "SIZE_LIMIT_EXCEEDED"`, while the upload path reports HTTP `413 FILE_TOO_LARGE` | Two different layers, deliberately two different strings — a job's `error.code` is never the API's 413 code, so branching on the code can always tell them apart |
| 3 | README 9.7's rule example shows integer bounds (`min: 16`); the stored and echoed form is `16.0` | Display-only; validation behaviour is identical. Compare bounds numerically, not as strings |
| 4 | README 11.4 offers "failed input job **or** early 422" for a header-only file; the code always chooses the early 422 | It arrives as `422` with `error.code = "EMPTY_DATASET"`, not `VALIDATION_ERROR` |
| 5 | README 9.7 does not name the upload-time CSV validation codes beyond "early CSV checks" | §2 lists all seven that can appear on `POST /api/jobs` |
| 6 | `POST /api/rule-sets` and `GET /api/jobs` are registered without a trailing slash | `/api/rule-sets/` gets a 307 redirect to `/api/rule-sets`; always call the exact path |
| 7 | README 13.5 describes `failure_kind=INPUT` for malformed CSV; the concrete `error.code` is the validator's code | Read `error.failure_kind` for the category and `error.code` for the specific cause |
