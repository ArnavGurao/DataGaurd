# DataGuard frontend

A complete React + Vite frontend implementing the root project guide. Start with the interactive demo; connect the same screens to FastAPI when the backend is ready.

## Run on Windows

```powershell
Set-Location 'C:\Users\ARNAV GURAO\OneDrive\Desktop\DataGaurd\Frontend'
npm.cmd install
npm.cmd run dev
```

Open **http://127.0.0.1:5173**. Use `npm.cmd` if PowerShell blocks the `npm.ps1` wrapper. Dependencies and a lock file are included; teammates can use `npm.cmd ci`.

Node 24 was used to build and test this project. The development server stays on port 5173 and proxies `/api` to `http://127.0.0.1:8000`.

## Included screens

- Overview: actual demo totals, daily quality chart, recent datasets, workspace JSON export.
- Dataset history: text search, status filters, sorting, pagination, activity details.
- Upload: drag and drop, file chooser, title and rule selection, file type/size checks.
- Rule sets: view, create, and duplicate sets; arbitrary numeric ranges and allowed-value checks.
- Job details: waiting/running/failed/completed states, automatic polling, counts, per-rule failures, 100-record error preview, saved rule snapshot.
- Downloads: original CSV, valid rows, rejected rows with reasons, JSON report.
- Authentication: registration, sign-in, password visibility, expired-session recovery.
- Help and workspace settings.

The responsive interface uses locally bundled DM Sans and Manrope fonts, Lucide icons, a CSS illustration, and an SVG chart. It does not rely on remote image/font services.

## Demo mode

The default demo opens directly into a labeled workspace with seven synthetic datasets and three rule presets. Demo uploads are actually checked in the browser against the selected rules. Waiting/running transitions simulate background processing; they are not an AWS worker.

Use **Upload dataset → Use sample dataset → Validate dataset**. The README fixture produces **6 total / 2 valid / 4 rejected / 33.33% valid** (rounded to 33.3% in the score display), with five rule failures. Download a corrected sample from the upload or help screen, submit it, and compare the reports.

Demo changes and uploads stay in JavaScript memory. Reloading the page resets them. No demo files, login tokens, or passwords are written to browser storage. Use synthetic files. Opening a demo is not authentication and grants no backend access.

## Connect FastAPI

For a temporary live session, click **Connect backend** and sign in to your running FastAPI server.

To make API sign-in the default on reload:

```powershell
Copy-Item .env.example .env
```

Set `VITE_DATA_MODE=api` in `.env`, then restart Vite. For production, set this variable before building. It is a public frontend mode flag, not a secret.

The live adapter in `src/api.js` uses the original guide's `/api` routes and snake_case fields. Tokens remain in React memory. FormData uploads do not set Content-Type manually. API errors and FastAPI 422 errors are normalized, and 401 responses clear the session. Polling uses a request-then-timeout loop every three seconds and aborts on unmount; it stops on completed/failed/error states. Rule and job ownership must be enforced by the backend.

See [the frontend/API contract](../docs/api-contract.md) for agreed field shapes, including report fields that the original README described without a complete JSON example. The backend must validate uploads and rules independently; browser validation is only a convenience. No backend, authentication server, durable worker, database, or AWS resources were created as part of this frontend.

## Build and verify

```powershell
npm.cmd test
npm.cmd run test:e2e
npm.cmd run build
npm.cmd run preview
```

- Unit tests cover exact sample counts, all duplicate occurrences, missing columns, original-value preservation, numeric edge cases, CSV parsing, limits, and capped previews.
- Browser tests use installed Microsoft Edge in headless mode. They cover desktop/mobile layouts, upload-to-report workflows, downloads, rule creation, invalid files, failed jobs, schema errors, and mocked API authentication/expiry. Live backend behavior still needs integration testing when the backend exists.
- `test-results/` contains desktop/mobile screenshots and traces for failed tests; it is ignored by Git.
- The production output is `dist/`. Serve it through Nginx with `/api` forwarded to FastAPI as described in the root README. Hash routes support refresh without server-side route rewrites.

CSV downloads preserve raw original values, including formula-like strings. They are machine-readable exports, not spreadsheet-sanitized copies.

## Main files

```text
src/
  App.jsx                 Session, navigation, shared data, sidebar, topbar
  api.js                  Authenticated API helper and live service adapter
  context.jsx             Shared context and navigation helpers
  styles.css              Responsive application styles
  components/UI.jsx       Tables, dialogs, buttons, status and error states
  pages/                  Overview, auth, upload, rules, history, report, guide
  lib/demo.js             Synthetic data and in-memory demo service
  lib/validation.js       Demo CSV parser, validator, and exports
  lib/validation.test.js  Validation behavior tests
tests/workflows.spec.js   End-to-end browser checks
```
