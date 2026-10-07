# DataGuard Backend

A practical run guide for teammates who just cloned the repository. Every command
below is PowerShell for Windows, and every command runs from the `backend`
directory unless it says otherwise.

## 1. What this service is

The backend is a FastAPI HTTP API that registers users, stores rule sets, accepts
CSV uploads, and reports job status and results. A **separate worker process**
validates each uploaded CSV against the frozen rule set and writes four artifacts
— the original file, `valid.csv`, `rejected.csv`, and `report.json`. All durable
state (users, rule sets, datasets, jobs, summaries, notification events) lives in
PostgreSQL; the API and the worker are two different processes that talk to each
other only through that database.

## 2. Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12 or newer | The guide (README 4.1, 9.1) says 3.12; **3.12+ is fine** and this machine has **3.13.7**. Verify with `py -3.13 --version`. |
| PostgreSQL 16 | Reachable at `127.0.0.1:5432`, with login role `dataguard_app` and database `dataguard` owned by it (README 9.3). |
| Git | To clone and pull. |

**Heads-up on this machine:** the `py` launcher finds Python 3.13.7, but the bare
`python` on `PATH` is 3.10.11 — too old. Use `py -3.13` (or `py`) to create the
virtual environment, never bare `python`. Likewise, in Windows PowerShell `curl`
is an alias for `Invoke-WebRequest` and does **not** understand `-F`; use
`curl.exe` (see section 7).

### Docker alternative for PostgreSQL

If you would rather not install PostgreSQL, one command gives you exactly the
role and database README 9.3 asks for (pick your own password):

```powershell
docker run -d --name dataguard-postgres -e POSTGRES_USER=dataguard_app -e POSTGRES_PASSWORD=<pick-one> -e POSTGRES_DB=dataguard -p 127.0.0.1:5432:5432 postgres:16
```

`POSTGRES_USER=dataguard_app` creates that login role, `POSTGRES_DB=dataguard`
creates the database, and it is owned by that role — so this single command
satisfies the "role `dataguard_app`, database `dataguard` owned by it"
requirement. Put the same password into `DB_PASSWORD` in `.env`. Confirm it is
up with `docker ps` (or `docker logs dataguard-postgres`).

## 3. First-time setup

From `backend`:

```powershell
Set-Location C:\Users\HP\Desktop\DataGuard\backend

# 1. Create the virtual environment (3.12+; this machine's launcher default is 3.13)
py -3.13 -m venv .venv

# 2. Upgrade pip inside it
.\.venv\Scripts\python.exe -m pip install --upgrade pip

# 3. Install the pinned dependencies
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. Create your local .env from the example
Copy-Item .env.example .env

# 5. Generate a JWT secret and write it into .env in one step
$secret = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
(Get-Content .env) -replace '^JWT_SECRET=.*', "JWT_SECRET=$secret" | Set-Content .env
```

Then open `.env` and set `DB_PASSWORD` to the password you chose for the
`dataguard_app` role (or the Docker command in section 2). Use the explicit
`.\.venv\Scripts\python.exe` form for every command — README 9.1 requires it
because it sidesteps Windows execution-policy problems with
`Activate.ps1`.

> `.env` is git-ignored (`.gitignore` ignores `.env` and `.env.*`, keeping only
> `.env.example`). Never commit it, never paste its contents into evidence, and
> never put the JWT secret in the frontend.

The two placeholder values shipped in `.env.example`
(`replace-with-random-local-secret`, `replace-with-local-password`) are
deliberately rejected by `app/config.py` — if you leave either in place, the
process refuses to start with a `Missing or placeholder settings` error.

## 4. Running it

Use three terminals; all of them start in `backend` after the one-time migration
in section 5 and the seed in section 6 (or step 3 of section 7).

**Terminal A — the API:**

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/api/health` (liveness), `http://127.0.0.1:8000/api/ready`
(readiness — runs `SELECT 1`), and the interactive docs at
`http://127.0.0.1:8000/docs`.

**Terminal B — the worker:**

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

**Terminal C — the seed script (one-shot, run once):**

```powershell
.\.venv\Scripts\python.exe -m app.seed --email demo@example.com --password demo-password
```

The seed is idempotent: re-running it reuses the existing account and leaves an
existing `Student Records` rule set unchanged.

### Why the worker is a separate process

This is the whole point of the design, not an accident (README 13.2, 3.1):

- The API and the worker are **different processes** that communicate through
  PostgreSQL. The committed `PENDING_DISPATCH` row *is* the durable dispatch
  record; there is no in-memory Python list shared between them (an in-memory
  list would silently make the "separate worker" claim false).
- The API stays fast: the upload handler does only the cheap, certain checks
  (ownership, size, extension, header sanity), commits the job, and returns
  `202` immediately. Row validation happens later, in the worker.
- **Pausing the worker must still let jobs reach the queue.** With the worker
  stopped, uploads keep returning `202` and jobs pile up in `PENDING_DISPATCH`;
  the API's background publisher keeps flipping them to `QUEUED` every few
  seconds. Restart the worker and it drains the backlog. If stopping the worker
  also stopped uploads from being recorded, process separation would be broken.

## 5. Database migrations

From `backend`, after your database is reachable and `.env` is filled in:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

- The connection URL is built from the application settings in
  `migrations/env.py`, never from `alembic.ini`, so no password is ever stored
  in a committed file (README 9.5).
- Teammates who **pull** the repository run `upgrade head`. Do **not** regenerate
  the initial migration — `migrations/versions/036e986f36f3_initial_schema.py`
  already exists and is shared; regenerating it would fork the schema history.
- Run migrations **once at deploy time**. The API and worker do **not** run
  migrations on startup; they assume the schema is already current.
- Only after you change `app/models.py` do you create a *new* revision,
  review the generated file, and commit it:

  ```powershell
  .\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe the change"
  .\.venv\Scripts\python.exe -m alembic upgrade head
  ```

## 6. Running the tests

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

`pytest.ini` puts `backend/` on `sys.path`, limits collection to `tests/`, and
runs quietly. The suite needs **no database and no AWS credentials**: the
validation engine takes its limits as arguments, so the core behaviour is tested
in isolation against plain bytes.

The suite covers the central behaviour (README 11.5): the dirty-CSV counts,
duplicate semantics (both occurrences of a repeated ID are rejected), missing
columns, preserved identifiers (leading zeros, literal `NA`), multiple errors on
one row, malformed input, and numeric edge cases. The relevant files are
`tests/test_validator.py`, `tests/test_rules.py`, `tests/test_reports.py`, and
`tests/test_storage.py`, with shared fixtures and the demo rule set in
`tests/conftest.py`.

## 7. A five-minute end-to-end check

This proves the whole local system works: API, worker, database, storage, and the
validation engine together.

**Step 1 — migrate.** From `backend`:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

**Step 2 — seed the demo account.** From `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.seed --email demo@example.com --password demo-password
```

This creates the account and a rule set named **Student Records** using the
sample rule set from README 9.7 (required columns `student_id, name, age,
department`; `student_id` unique; `age` an integer 16–100; `department` one of
`COMP, IT, EXTC`).

**Step 3 — start the API and the worker**, each in its own terminal, from
`backend` (section 4, Terminals A and B). Wait for the worker to log
`Worker started`.

**Step 4 — log in and grab a token.** In a PowerShell window at the repository
root (`C:\Users\HP\Desktop\DataGuard`), using `curl.exe`:

```powershell
$token = (curl.exe -s -X POST http://127.0.0.1:8000/api/auth/login `
    -H "Content-Type: application/json" `
    -d '{"email":"demo@example.com","password":"demo-password"}' `
    | ConvertFrom-Json).access_token
```

**Step 5 — find the seeded rule set's id:**

```powershell
$rules = curl.exe -s http://127.0.0.1:8000/api/rule-sets `
    -H "Authorization: Bearer $token" | ConvertFrom-Json
$ruleSetId = ($rules | Where-Object { $_.name -eq "Student Records" }).id
$ruleSetId
```

**Step 6 — upload the dirty sample against that rule set:**

```powershell
$accepted = curl.exe -s -X POST http://127.0.0.1:8000/api/jobs `
    -H "Authorization: Bearer $token" `
    -F "title=Dirty upload" `
    -F "rule_set_id=$ruleSetId" `
    -F "file=@../samples/students_dirty.csv;type=text/csv" `
    | ConvertFrom-Json
$accepted
```

The response is **`202 Accepted`** in the shape
`{"job_id": "...", "status": "PENDING_DISPATCH"}`. The `job_id` is what you poll.

**Step 7 — poll until the worker finishes** (a second or two for a six-row file):

```powershell
$job = curl.exe -s "http://127.0.0.1:8000/api/jobs/$($accepted.job_id)" `
    -H "Authorization: Bearer $token" | ConvertFrom-Json
$job.status
$job.summary
```

**Expected outcome for `students_dirty.csv`:** status `COMPLETED`, and

```text
total_rows      : 6
valid_rows      : 2
rejected_rows   : 4
valid_percentage: 33.33
```

(The four rejected rows fail one or more rules: a blank `name`, both `S003`
occurrences, an out-of-range `age`, and an unknown `department`. One row fails
twice, so per-rule failure counts sum to 5 while rejected rows are 4 — that is
expected, not a bug.)

**Step 8 — confirm the second outcome.** Uploading `samples/students_good.csv`
the same way gives `6` valid rows and `0` rejected.

```powershell
$good = curl.exe -s -X POST http://127.0.0.1:8000/api/jobs `
    -H "Authorization: Bearer $token" `
    -F "title=Good upload" `
    -F "rule_set_id=$ruleSetId" `
    -F "file=@../samples/students_good.csv;type=text/csv" `
    | ConvertFrom-Json
Start-Sleep -Seconds 2
(curl.exe -s "http://127.0.0.1:8000/api/jobs/$($good.job_id)" `
    -H "Authorization: Bearer $token" | ConvertFrom-Json).summary
```

To download an artifact, ask for it by `kind` (`original`, `valid`, `rejected`,
`report`); the API returns a short-lived signed URL that the browser can follow
without the `Authorization` header:

```powershell
curl.exe -s "http://127.0.0.1:8000/api/jobs/$($accepted.job_id)/downloads/rejected" `
    -H "Authorization: Bearer $token"
```

## 8. Project layout

```text
backend/
  requirements.txt        Pinned backend dependencies
  pytest.ini              Test config: pythonpath=., testpaths=tests
  alembic.ini             Alembic config; DB URL intentionally left blank
  .env.example            Template for the git-ignored .env
  migrations/
    env.py                Builds the DB URL from app settings, imports model metadata
    versions/             Committed migration scripts (do not regenerate the initial one)
  app/
    main.py               FastAPI app; /api/health and /api/ready; owns the publisher
    config.py             Typed settings from .env (local) or Parameter Store (AWS)
    database.py           Lazy engine, session factory, declarative Base
    models.py             SQLAlchemy tables: users, rule_sets, datasets, jobs, notification_events
    schemas.py            Pydantic request/response models (snake_case contract)
    auth.py               Argon2 hashing, JWT issue/verify, owner scoping
    errors.py             One structured {"error": {code, message}} shape for every failure
    routers/
      auth.py             Register, login, /me
      rules.py            Create and list rule sets (validated on write)
      jobs.py             Upload, status, list, report, download-url endpoints
      downloads.py        Signed local-mode artifact download
    services/
      validator.py        Deterministic CSV parse + rule engine
      rules.py            Rule-set parsing, validation, frozen job snapshots
      reports.py          Builds valid.csv, rejected.csv and report.json
      storage.py          Storage interface + local implementation (S3 behind it)
      dispatcher.py       Queue adapters (database/SQS) and the API-side publisher
      notifier.py         Durable, best-effort operator notifications
    worker.py             Separate process: poll, claim lease, validate, write, complete
    seed.py               Creates the demo account and Student Records rule set
  tests/                  Pytest suite (no DB, no AWS needed)
  runtime/                Ignored: local storage and logs
samples/                  Fixture CSVs (dirty, good, malformed, missing column, ...)
```

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Missing or placeholder settings: JWT_SECRET, DB_PASSWORD` | `.env` is absent, or still contains the `replace-with-*` placeholders that `config.py` rejects. | Copy the template (`Copy-Item .env.example .env`) and fill in a real `JWT_SECRET` and `DB_PASSWORD` (section 3). |
| `GET /api/ready` returns `503` | The readiness check runs `SELECT 1`; the database is unreachable. `/api/health` still returns `200` because it is liveness only. | Confirm PostgreSQL is running and reachable at `127.0.0.1:5432`; re-check `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`. With Docker: `docker ps` / `docker start dataguard-postgres`. |
| Jobs stay in `PENDING_DISPATCH` | The worker isn't running, or the API's publisher thread isn't dispatching (README 13.2, 13.3). | Start the worker in its own terminal (`.\.venv\Scripts\python.exe -m app.worker`). Check the API log for publisher errors. Note uploads still return `202` while the worker is paused — that is expected, and jobs should drain once it starts. |
| PowerShell error about execution policy when activating the venv | `Activate.ps1` is blocked on this machine. | Don't activate — use the explicit interpreter path, `.\.venv\Scripts\python.exe`, exactly as README 9.1 requires. |
| `No suitable Python runtime found` for `py -3.12` | 3.12 isn't installed here; 3.13.7 is. | Use `py -3.13 -m venv .venv` (or `py -m venv .venv`). Any 3.12+ interpreter is fine. |
| `python` installs packages into the wrong place, or syntax errors in imports | Bare `python` on this machine is 3.10.11. | Always use `.\.venv\Scripts\python.exe` for every backend command. |
| `curl: option -F: is unknown` or unexpected HTML output | In Windows PowerShell `curl` is an alias for `Invoke-WebRequest`. | Use `curl.exe` explicitly. |
| `No such file or directory: alembic.ini` | Alembic was run from the wrong directory. | Run it from `backend`; `script_location` is relative to `alembic.ini`. |
| Upload returns `404` "Rule set not found" | The token's owner doesn't own that rule set (ownership is checked before storing anything). | Log in as the seeded account and use the id from `GET /api/rule-sets` for *that* account. |
| Upload returns `413` or `422` | File exceeds 5 MiB, wrong extension, or a bad header row. | The upload handler rejects the cheap cases up front; the worker handles row-level validation. |
