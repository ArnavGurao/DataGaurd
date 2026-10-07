# Coordination guide

Written by **B (backend)** on 2026-10-07, for the three-way split in README §5.
Its job is to let A and C work without waiting on each other, and to record the
decisions that are already frozen so nobody re-litigates them in week 3.

Read alongside:

- [`docs/api-contract.md`](api-contract.md) — the A ↔ B interface in full
  (every route, request shape, response example, and error code).
- `backend/README.md` — prerequisites, setup, running the three processes.
- README §5 (roles), §6 (checkpoints), §13 (background jobs).

---

## 1. Where each workstream stands

| Owner | Area | State |
|---|---|---|
| **A** | Frontend | **Unblocked — start now.** The contract is frozen (§3). |
| **B** | Backend | Local milestone complete. Branch `feature/backend`, PR #1. |
| **C** | Cloud/integration | Not started. Interfaces and stubs are ready (§4). |

Nothing has run against AWS. `S3Storage`, `SqsJobQueue` and `SnsNotifier` are
stubs that raise `NotImplementedError` naming their entry points, so a
misconfiguration fails loudly instead of silently writing to local disk.

### What is actually verified

- 133 tests pass with neither a database nor AWS configured.
- End-to-end against local PostgreSQL 16: `samples/students_dirty.csv` yields
  6 total / 2 valid / 4 rejected / 33.33%, matching README §11.3;
  `students_good.csv` yields 6 / 6 / 0 / 100%.
- The signed download URL, fetched with **no** `Authorization` header, returns
  `text/csv` — the `window.location.assign` path A depends on.
- README §13.8 crash recovery: a job forced into the state a killed worker
  leaves behind (`RUNNING`, expired lease) is re-delivered and completes.
- An idle worker consumes 0.00s CPU over 6 seconds.

Reproduce any of these with `backend/scripts/smoke_test.py` and
`backend/scripts/check_crash_recovery.py`.

---

## 2. Running it locally

```powershell
# Once
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env      # then fill in JWT_SECRET and DB_PASSWORD
docker compose up -d             # PostgreSQL 16 on 127.0.0.1:5432
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seed --email demo@example.com --password demo-password-123

# Every session — three processes, three terminals
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m app.worker
.\.venv\Scripts\python.exe -m pytest -q
```

`py -3.12` is not installed on B's machine; `py -3.13` is used throughout.
On Windows, PowerShell aliases `curl` to `Invoke-WebRequest` — use `curl.exe`
for multipart uploads, or plain `httpx` as the scripts do.

`docker-compose.yml` lives in `backend/` and is run from there, so Compose reads
the gitignored `backend/.env` for the role, database and password — no
credentials in the committed file. `DB_PASSWORD` is required, so a missing
`.env` fails immediately with an actionable message rather than starting a
database whose password doesn't match the app's.

If you already have a Postgres container named `dataguard-postgres` from a
manual `docker run`, Compose will refuse to start because the name is taken.
Either stop and remove that container first, or keep using it and skip the
Compose step — the connection settings are identical either way.
`docker compose down` keeps the data; `docker compose down -v` deletes it and
you re-run `alembic upgrade head` plus `app.seed`.

---

## 3. Interface A ↔ B (frontend ↔ backend)

**Full detail: [`docs/api-contract.md`](api-contract.md).** The frozen decisions:

| Decision | Value |
|---|---|
| Base path | `/api` — always relative, never an absolute host |
| Case | `snake_case` **everywhere**. No `camelCase` field exists. |
| Upload | `POST /api/jobs`, multipart fields `title`, `rule_set_id`, `file` → **202** |
| Poll | `GET /api/jobs/{job_id}` every **3 s**; stop on `COMPLETED`/`FAILED` and on unmount |
| Statuses | `PENDING_DISPATCH`, `QUEUED`, `RUNNING` (poll) · `COMPLETED`, `FAILED` (terminal) |
| Errors | Always `{"error": {"code", "message"}}`, for every failure including 500 |
| Downloads | `GET /api/jobs/{job_id}/downloads/{kind}` → `{url, filename, expires_in}`, then `window.location.assign(result.url)` |
| Download kinds | `original`, `valid`, `rejected`, `report` (only those in `available_downloads`) |
| Token | Keep in React memory, never `localStorage`. 30-minute expiry — handle 401 by re-login. |
| Not-ready report | `409` while the job is not `COMPLETED` |

Routes that exist today: `POST /api/auth/register`, `POST /api/auth/login`,
`GET /api/auth/me`, `POST|GET /api/rule-sets`, `POST|GET /api/jobs`,
`GET /api/jobs/{id}`, `GET /api/jobs/{id}/report`,
`GET /api/jobs/{id}/downloads/{kind}`, `GET /api/downloads/{token}`,
`GET /api/health`, `GET /api/ready`.

Three things that will cost A time if missed:

1. **`valid_percentage` is rounded to 2 dp**, and per-rule failure counts do
   **not** sum to `rejected_rows` — one row can fail several rules (the demo
   file has 5 failures across 4 rejected rows). Show the explanation, don't
   reconcile the arithmetic.
2. **The signed download URL carries its own authorisation.** Do not attach the
   bearer token; do not fetch it with `fetch()` and rebuild a blob unless
   testing — `window.location.assign` is the intended path. Never log or
   screenshot the URL (§12.3).
3. **`numeric_ranges` bounds echo back as floats** (`16.0`, not `16`). Cosmetic
   for display; don't write logic that assumes an int.

If A needs a field that isn't there, ask B — never derive it. Contract changes
go through §7.

---

## 4. Interface B ↔ C (backend ↔ cloud)

C implements three interfaces. **Do not change their signatures** — B's worker
and API call them as-is, and the local implementations are what the whole
test suite runs against.

All three live behind selectors in `backend/app/services/` that read
`STORAGE_MODE` / `QUEUE_MODE` with **no silent fallback**: an unknown value
raises rather than degrading to local.

### 4.1 `StorageBackend` — `app/services/storage.py`

```python
save_bytes(key: str, data: bytes) -> None        # overwrite semantics
read_bytes(key: str) -> bytes                    # MUST raise FileNotFoundError when absent
delete_object(key: str) -> None                  # deleting a missing key is NOT an error
exists(key: str) -> bool
get_download_url(key: str, filename: str, ttl_seconds: int) -> str
```

- `read_bytes` raising `FileNotFoundError` is load-bearing: the worker maps it
  to a `FAILED`/`INPUT` outcome. An S3 implementation must translate
  `NoSuchKey` (and a 404 `HeadObject`) into `FileNotFoundError`, not
  `ClientError`.
- `get_download_url` is a presigned GET. The browser fetches it with no auth
  header, so the object must not require one.
- **Key layout is fixed and must stay opaque to callers** (README §12.1):
  `uploads/{owner_id}/{dataset_id}/original.csv` and
  `outputs/{owner_id}/{job_id}/{valid|rejected}.csv`, `.../report.json`.
  Keys are generated server-side from IDs only — no API accepts a path or
  bucket, and C must not add one.
- `delete_object` is called on upload cleanup (§13.7) for a key that may
  already exist from a previous attempt.

### 4.2 `JobQueue` — `app/services/dispatcher.py`

```python
send(job_id: uuid.UUID) -> None
receive(*, wait_seconds: int, max_messages: int) -> list[QueueMessage]
delete(message: QueueMessage) -> None
```

- **The message body carries `job_id` only** (§13.4 step 2). Paths and rules are
  loaded from RDS by the worker; never put them in the message.
- `receive` long-polls: `WaitTimeSeconds=20`, `MaxNumberOfMessages=1`.
- `delete` acknowledges **only after** the outcome transaction commits
  (§13.4 step 9).
- Delivery is **at-least-once**. Duplicates arrive normally: the publisher can
  send twice if its conditional DB update fails after a successful send, and
  SQS redelivers. The worker's claim logic tolerates this — do not try to make
  delivery exactly-once.
- **Terminal failures are deliberately left unacknowledged** so redrive can
  move them to the DLQ. Only `COMPLETED` duplicates and `FAILED`/`INPUT` are
  acknowledged. This is only correct if the redrive policy below is configured
  — a FAILED job with no DLQ would be retried forever.

Values to configure, from README §13.3–13.5:

| Setting | Value |
|---|---|
| Queue visibility timeout | 150 s initially (§13.4) |
| Redrive `maxReceiveCount` | 3 (§13.5) |
| Boto3 connection/read timeouts | bounded, with a small retry budget |

The DB lease (`LEASE_SECONDS`, default 120 s) and the SQS visibility timeout are
**separate mechanisms** and must stay consistent — for work longer than a
minute, extend both while processing (§13.4). Keep demo jobs well under a
minute.

### 4.3 `Notifier` — `app/services/notifier.py`

```python
send(event: NotificationEvent, job: Job) -> None
flush_pending() -> int          # retry pending events; returns how many sent
```

- Events are written in the **same transaction** as the job outcome
  (§13.6), so a notice cannot exist for a job that rolled back.
- `flush_pending` is called by the API publisher loop independently of
  validation. An SNS outage must never turn a `COMPLETED` audit into `FAILED`
  — the worker never calls the notifier inline.
- A failed send stays `PENDING` and is retried; it is never marked terminal.
- **No CSV contents and no credentials in notification bodies** (§13.6).
  Include job ID and event ID so a duplicate notice is understandable.

### 4.4 Configuration C owns

`APP_ENV=aws` **requires** `STORAGE_MODE=s3` and `QUEUE_MODE=sqs`; the app
refuses to start otherwise. That guard exists because the defaults are the local
values, and a cloud deploy that forgot them would quietly write uploads to a
container filesystem and lose them (§12.1).

In AWS mode settings come from Parameter Store `SecureString` under
`SSM_PARAMETER_PREFIX` (default `/dataguard`), **not** from `backend/.env`:
`JWT_SECRET`, `DB_PASSWORD`, and the resource names below. boto3 uses the
instance role — no embedded credentials, ever.

`AWS_REGION`, `S3_BUCKET_NAME`, `SQS_QUEUE_URL`, `SNS_TOPIC_ARN`,
`SSM_PARAMETER_PREFIX`. Non-secret tuning values and their defaults are listed
in `backend/.env.example`.

---

## 5. Divergences from the README (decision log)

Known and deliberate — recorded so they aren't rediscovered as bugs. Also in
`docs/api-contract.md` §9.

| # | Divergence | Why |
|---|---|---|
| 1 | `jobs.title` and `notification_events.sent_at` exist but aren't in §9.5's table | The API response example includes `title`; the table is incomplete |
| 2 | `rule_sets` has an extra `UNIQUE(owner_id, name)` | Backs the 409 on duplicate rule-set names |
| 3 | `failure_kind=INTERNAL` is defined in the model and constraint but never assigned | Keeps §13.5's table exact; unexpected exceptions use `RETRY_EXHAUSTED` + `error_code=INTERNAL_ERROR` |
| 4 | A worker-side oversize failure reports `SIZE_LIMIT_EXCEEDED`, not `FILE_TOO_LARGE` | `FILE_TOO_LARGE` is the HTTP 413 upload code (§9.7); the two must stay distinguishable |
| 5 | `/api/ready`'s 503 uses code `STORAGE_UNAVAILABLE` for a database outage | §9.7 maps 503 to "dependency unavailable" as one status; the message names the database |
| 6 | Routes are registered without a trailing slash | A trailing-slash request 307-redirects; use the exact paths |

---

## 6. Open decisions — need a human call

1. **Blank values in a unique column count as duplicates.** README §11.2 says
   `keep=False`, and the code follows it exactly — so a file with three blank
   optional IDs rejects all three as duplicates. Defensible but surprising.
   Changing it is a one-line change in the validation engine; it is a product
   decision, not a bug, so it has been left alone.
2. **Demo account and rule set** — currently seeded ad hoc with
   `app.seed`. Decide the credentials and the demo rule set before the week-2
   demo so A and C can script against them.
3. **Who owns the RDS migration** — README §6 week 3 gives it to B, C creates
   the instance. Agree the handoff: C provisions, B runs `alembic upgrade head`.

---

## 7. How to change the contract

1. A and B agree the change **before** either codes against it (§5 working
   agreements).
2. B updates `docs/api-contract.md` in the same PR as the code, so the doc is
   never behind.
3. B posts the change in the daily check-in — A should never discover a shape
   change from a failing screen.
4. `main` stays releasable; work goes on small feature branches with reviewed
   PRs (§5 working agreements 3).

Never commit secrets, `.env`, private keys, or real personal data. Signed
download URLs are temporary bearer capabilities — don't log or screenshot them.

---

## 8. Checkpoints

Per README §6, the local milestone ("full browser workflow works locally") is
what week 2 targets, and it needs A's screens wired to this API — B's half of
it is done and verified. The AWS milestone (week 3) starts once the local one
passes end-to-end in a browser.

Do not postpone the first AWS database connection to the final week (README
§6). C can start on permissions, budget and the VPC now — the adapters in §4
are the only part that needs to wait for the local milestone.
