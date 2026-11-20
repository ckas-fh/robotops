# RobotOps

A synthetic robotics operations dashboard built with Python, Streamlit, FastAPI and SQLite. It simulates a 20-robot fleet at a single site, San Francisco Lab, so you can explore fleet health, inspections, maintenance and teleoperation sessions. The Streamlit UI gets **all** of its data through a REST API. An optional GitHub Issues integration needs a person to approve each issue before it's created.

It connects to no hardware and uses no paid APIs. All robot data is synthetic.

## Architecture

```
Browser ──> Streamlit UI (app.py, views/) ──HTTP──> FastAPI (api/main.py) ──> robotops/ domain ──> SQLite
                                                          │
                                                          └──(optional, human-approved)──> GitHub Issues API
```

- The UI never touches the database. It calls the API through `robotops/client.py`, sending the visitor's session id in the `X-RobotOps-Session` header so each visitor keeps an isolated dataset.
- When `ROBOTOPS_API_URL` isn't set, Streamlit starts the API itself with uvicorn on a random `127.0.0.1` port (**embedded mode**). The UI still talks to it over real HTTP, so a single command runs everything.

## Features

| Page | What it does |
|---|---|
| **Fleet dashboard** | KPI cards (available, down, in maintenance, open tickets), status-by-model chart, filterable robot table, status changes, and teleoperation sessions (past 7 days / today / next 7 days). |
| **Inspections & maintenance** | KPI cards, filterable open-ticket table, inspection schedule, and forms to log inspections, open tickets, update or close tickets, and **send a ticket to GitHub Issues**. |

### Business rules
- A **Fail** inspection opens a High ticket and moves the robot to *Maintenance*. **Needs attention** opens a Medium ticket.
- Opening a **Critical** ticket marks the robot *Down*.
- Closing a robot's last open High/Critical ticket returns it to *Operational*.
- Back-dated inspections never reset a robot's inspection clock.

## Local setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                   # run the test suite
```

**Option A: one command (embedded API)**
```bash
streamlit run app.py                        # http://localhost:8501
```

**Option B: API and UI as separate processes**
```bash
uvicorn api.main:app --port 8000            # API docs: http://localhost:8000/docs
ROBOTOPS_API_URL=http://127.0.0.1:8000 streamlit run app.py
```

**Option C: Docker Compose (two containers)**
```bash
cp .env.example .env                        # optional: edit settings
docker compose up --build                   # UI :8501, API :8000 (bound to localhost)
```

The sidebar shows which API the UI is using, with a link to its interactive docs.

## REST API

All `/api/v1` routes need `X-RobotOps-Session: <32 hex chars>` in demo mode. If `ROBOTOPS_API_KEY` is set, they also need `X-API-Key`. Interactive docs are at `/docs`.

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness, data mode, GitHub integration mode (never the token). |
| `GET /api/v1/fleet/status` | Fleet KPIs and every robot's state. |
| `PATCH /api/v1/robots/{name}/status` | Set a robot's status. |
| `GET /api/v1/issues` | Robot maintenance issues. Filters: `priority`, `status`, `robot`, `include_closed`. |
| `GET /api/v1/issues/{id}` | One issue. |
| `POST /api/v1/issues` | Open an issue. A Critical issue takes the robot Down. |
| `PATCH /api/v1/issues/{id}` | Update status/assignee. `Closed` needs a `resolution`. |
| `POST /api/v1/inspections` | Log an inspection. A Fail or Needs attention result auto-opens a ticket. |
| `GET /api/v1/teleop/sessions` | Teleoperation sessions. `window=past\|today\|upcoming\|all`, `days`. |
| `POST /api/v1/session/reset` | Re-seed the caller's synthetic dataset. |
| `GET /api/v1/integrations/github` | GitHub mode and the reason it's in dry run. |
| `GET /api/v1/issues/{id}/github/preview` | The exact issue that would be sent. |
| `POST /api/v1/issues/{id}/github` | Create the GitHub issue. Body `{"approved": true, "dry_run": false}`. |

```bash
curl -H "X-RobotOps-Session: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" http://localhost:8000/api/v1/fleet/status
```

### Error handling

| Status | When |
|---|---|
| `400` | Invalid input or a broken business rule |
| `401` | Bad API key |
| `403` | GitHub creation without approval |
| `404` | Unknown robot or issue |
| `409` | Already closed or already linked |
| `422` | Schema validation failure |
| `502` / `504` | GitHub failures and timeouts |

Unexpected errors return a generic `500` and are logged on the server; internals never reach the response. The UI client turns all of these, plus connection and timeout failures, into readable messages, and pages offer a **Retry** button when the API is unreachable.

## GitHub Issues integration (optional)

The integration is safe by default. Each layer must pass before anything reaches GitHub:

1. **Dry run by default.** Live mode needs *all* of the following:
   - `ROBOTOPS_GITHUB_DRY_RUN=false`
   - `GITHUB_TOKEN`
   - `ROBOTOPS_GITHUB_REPO`
   - `ROBOTOPS_MODE=shared`

   Otherwise the API returns the exact payload it *would* send, and no request is made. The UI states the reason.
2. **Never live in the public demo.** In per-visitor demo mode, anyone visiting could approve an issue, so live mode is refused there.
3. **Human approval.**
   - The UI shows the full issue preview (title, labels, body, target repo).
   - The create button stays disabled until a person ticks "I reviewed this issue and approve…".
   - The API rejects any request without `"approved": true` with `403`.
4. **No duplicates.** A ticket links to a single GitHub issue, and a second attempt returns `409`.

Live-mode setup (private, single-team use):
```bash
export GITHUB_TOKEN=github_pat_...                  # fine-grained, ONE repo, "Issues: Read and write" only
export ROBOTOPS_GITHUB_REPO=your-user/robotops-sandbox
export ROBOTOPS_GITHUB_DRY_RUN=false
export ROBOTOPS_MODE=shared
uvicorn api.main:app --port 8000
```

### Credential security
- **Where the token lives:**
  - It is read from the environment by the API process only, and is never sent to the browser.
  - No endpoint returns it; `/health` reports only `token_configured: true/false`.
  - It is excluded from `repr()` and redacted from error messages.
  - In Docker Compose, the `web` container has `GITHUB_TOKEN` blanked.
- **Keeping it out of git:**
  - `.env` is git- and docker-ignored, and `.env.example` contains no secrets.
  - Tests unset `GITHUB_TOKEN` and related variables, so a developer's real credentials are never used.
- **Optional shared secret:** `ROBOTOPS_API_KEY` protects the API when it's exposed beyond localhost. It's compared in constant time.

## Per-visitor isolation (public demo safety)

By default (`ROBOTOPS_MODE=demo`), each browser session gets its own SQLite file:
- The file is created and seeded on the first API call.
- One visitor's edits never reach another visitor.
- **Reset data** re-seeds only your copy.
- Session files are purged after `ROBOTOPS_SESSION_TTL` seconds of inactivity (default 6 h), and at most `ROBOTOPS_MAX_SESSIONS` (default 300) are kept.
- Session ids are validated (32 hex chars), which blocks path traversal.
- Databases created by an older schema version are rebuilt automatically.

Set `ROBOTOPS_MODE=shared` for a single persistent database (`robotops.db`).

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ROBOTOPS_API_URL` | *(unset → embedded)* | External API base URL for the UI |
| `ROBOTOPS_API_KEY` | *(unset)* | Shared secret required as `X-API-Key` |
| `ROBOTOPS_MODE` | `demo` | `demo` (isolated per visitor) or `shared` |
| `ROBOTOPS_DATA_DIR` | `<tmp>/robotops` | Where database files live |
| `ROBOTOPS_SESSION_TTL` | `21600` | Demo session idle expiry, in seconds |
| `ROBOTOPS_MAX_SESSIONS` | `300` | Cap on stored demo sessions |
| `GITHUB_TOKEN` | *(unset)* | GitHub token (API process only) |
| `ROBOTOPS_GITHUB_REPO` | *(unset)* | Target repository, `owner/repo` |
| `ROBOTOPS_GITHUB_DRY_RUN` | `true` | Set `false` to allow live mode |

## Simulated data
`robotops/seed.py` generates fictional data with a fixed random seed, and dates are relative to *now*. The data covers:
- 20 robots: 10 Kestrel mobile bases and 10 Heron manipulator arms
- 90 days of inspection history
- open and closed tickets with a consistent backstory
- teleoperation sessions from 3 days ago to 4 days ahead, with no robot or operator booked twice at once

## Tests
`pytest -q` runs 56 tests across five areas:
- **Domain rules:** maintenance and inspection logic.
- **API endpoints:** validation, isolation, error codes, API key.
- **GitHub client:** uses mocked HTTP and covers dry run, approval, success, 401/403/404/422/5xx, rate limits, timeouts and token redaction.
- **HTTP client:** network failures and error parsing.
- **Streamlit UI tests:** pages render through the embedded API, and the dry-run approval gate works.

No test touches the network or GitHub.

## Deployment
- **Streamlit Community Cloud:** point it at `app.py`. The API runs embedded, and GitHub stays in dry-run mode, which is right for a public demo.
- **Docker:** `docker build -t robotops . && docker run -p 8501:8501 robotops` runs a single container with the embedded API. Use `docker compose up` for separate API and UI containers.
- **Railway:** `railway init && railway up`, then generate a domain under the service's *Settings → Networking*. `railway.json` builds the Dockerfile (single service, embedded API) with a health check.
- **Heroku / Render:** the `Procfile` defines `web` (Streamlit) and `api` (uvicorn) processes.
- **CI:** `.github/workflows/tests.yml` runs the tests on every push.

## Project layout
```
app.py                 Streamlit entry: navigation, sidebar, reset
views/                 Pages + shared UI helpers (ui.py); all data via the REST API
api/
  main.py              FastAPI app: routes, validation, error handling, auth
  embedded.py          Runs the API inside Streamlit when no external URL is set
robotops/
  client.py            HTTP client used by the UI (error handling, headers)
  github.py            GitHub Issues integration (dry run, approval, redaction)
  db.py                Schema + connection/transaction helpers
  seed.py              Deterministic synthetic data
  maintenance.py       Inspections, tickets, robot status rules
  queries.py           Read models / KPIs (pandas)
  session.py           Per-visitor database isolation
tests/                 Domain, API, GitHub, client and Streamlit UI tests
```
