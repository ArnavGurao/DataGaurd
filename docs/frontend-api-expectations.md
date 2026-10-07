# DataGuard frontend integration contract

The implemented frontend follows section 9.7 of the root README. This file defines the remaining response details needed for backend integration. These are frontend expectations, not a claim that a backend is implemented.

## Transport and sessions

- Base path: `/api`, same origin. Local Vite proxies to `127.0.0.1:8000` without removing the prefix.
- Auth: `Authorization: Bearer <access_token>` on protected requests. Token stays in React memory.
- JSON error: `{ "error": { "code": "FILE_TOO_LARGE", "message": "Upload a CSV of 5 MiB or less." } }`. Default FastAPI `detail` validation arrays are also handled.
- 401 returns the user to login. 404 ends job polling and shows an actionable error. Other errors are shown with explicit retry; uploads are not automatically retried.

## Endpoints

| Method | Path | Body / response |
| --- | --- | --- |
| POST | `/auth/register` | JSON `{email,password}` → `{id,email}` |
| POST | `/auth/login` | JSON `{email,password}` → `{access_token,token_type}` |
| GET | `/auth/me` | `{id,email,name?}` |
| GET | `/rule-sets` | Array of rule sets, or `{items:[...]}` |
| POST | `/rule-sets` | JSON `{name,rules}` → created rule set |
| GET | `/jobs?limit=20&offset=0` | `{items:[job,...],total:7}`; include summary in each item |
| POST | `/jobs` | Multipart `title`, `rule_set_id`, `file` → 202 `{job_id,status}` |
| GET | `/jobs/{job_id}` | Job object below |
| GET | `/jobs/{job_id}/report` | Report object below |
| GET | `/jobs/{job_id}/downloads/{kind}` | `{url,expires_in:60}`; original/valid/rejected/report |

All listed endpoints except register/login require ownership-aware authentication. The frontend reads list pages using `total` and `offset` before applying local text/status filters and sorting. This provides accurate workspace aggregates for the mini-project without inventing unsupported API search parameters. A large production history should use server-side aggregates and filtering instead.

## Rule sets

```json
{
  "id": "rule-set-uuid",
  "name": "Student records",
  "description": "Optional description",
  "created_at": "2026-10-07T09:00:00Z",
  "rules": {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16, "max": 100, "integer": true}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]}
  }
}
```

## Job list/detail object

```json
{
  "job_id": "job-uuid",
  "title": "Student records audit",
  "original_name": "students_dirty.csv",
  "rule_set_id": "rule-set-uuid",
  "rule_set_name": "Student records",
  "status": "COMPLETED",
  "created_at": "2026-10-07T09:00:00Z",
  "completed_at": "2026-10-07T09:00:04Z",
  "summary": {"total_rows": 6, "valid_rows": 2, "rejected_rows": 4, "valid_percentage": 33.33},
  "available_downloads": ["original", "valid", "rejected", "report"],
  "error": null
}
```

`original_name` and `rule_set_name` are optional display metadata; the UI has fallbacks. Active jobs may have `summary: null`, `completed_at: null`, and no downloads. Failed jobs have `error: {code,message}` and may expose the original download.

Status values are `PENDING_DISPATCH`, `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`. Only the first three poll. COMPLETED can include invalid data; FAILED means processing could not finish. Missing-column audits complete with zero valid rows and dataset-level errors.

## Report

```json
{
  "schema_version": "1.0",
  "job_id": "job-uuid",
  "processed_at": "2026-10-07T09:00:04Z",
  "rules_snapshot": {
    "required_columns": ["student_id", "name", "age", "department"],
    "required_values": ["student_id", "name", "age", "department"],
    "unique_columns": ["student_id"],
    "numeric_ranges": {"age": {"min": 16, "max": 100, "integer": true}},
    "allowed_values": {"department": ["COMP", "IT", "EXTC"]}
  },
  "summary": {"total_rows": 6, "valid_rows": 2, "rejected_rows": 4, "valid_percentage": 33.33},
  "failure_counts": [
    {"code": "REQUIRED_VALUE", "column": "name", "message": "name must not be blank.", "count": 1},
    {"code": "DUPLICATE_VALUE", "column": "student_id", "message": "All duplicate occurrences are rejected.", "count": 2},
    {"code": "NUMERIC_RANGE", "column": "age", "message": "age must be an integer from 16 to 100.", "count": 1},
    {"code": "ALLOWED_VALUE", "column": "department", "message": "department must be one of: COMP, IT, EXTC.", "count": 1}
  ],
  "dataset_errors": [],
  "error_preview": [
    {
      "record_number": 2,
      "values": {"student_id": "S002", "name": "", "age": "21", "department": "IT"},
      "errors": [{"code": "REQUIRED_VALUE", "column": "name", "message": "name must not be blank."}]
    }
  ],
  "processing_duration_ms": 3200
}
```

The example includes one preview record for brevity. Return every rejected preview record up to a maximum of 100, each with all its errors. Record numbers count logical CSV data records starting at 1; quoted multiline fields do not increment them. `failure_counts` is an array grouped by code and column. `dataset_errors` uses `{code,column,message}` entries. Values in previews and exports remain strings.

Presigned or locally signed HTTP(S) download URLs are only generated after server-side token/owner/artifact checks. The browser navigates to the returned URL. A plain unsigned endpoint requiring an Authorization header is not sufficient for this contract.
