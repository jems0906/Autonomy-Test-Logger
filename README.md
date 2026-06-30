# Autonomy Test Logger

Autonomy Test Logger is a lightweight Python project for ingesting autonomous driving test logs, tagging driving scenarios, and tracking failures during engineering review.

## Features

- Ingest drive logs from CSV and JSON files
- Automatic scenario detection and tagging for:
  - Lane changes
  - Braking and hard acceleration
  - Cut-ins
  - Merges
  - Stop sign compliance and violations
- SQLite-backed storage of test runs, signals, events, and failure flags
- Streamlit dashboard for reviewing test runs and triaging failures
- Sidebar-configurable detection thresholds for braking, acceleration, steering, speed, and cut-ins
- Configurable policy engine with scenario pass/fail rules and automatic failure creation
- Event replay window for inspecting signals around a selected detected event
- Exportable engineering report bundle (`summary.json`, `summary.md`, `events.csv`, `failures.csv`)

## Tech Stack

- Python 3.11+
- Pandas, NumPy
- Plotly, Streamlit
- SQLite (via standard library `sqlite3`)

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the dashboard:

```bash
streamlit run streamlit_app.py
```

4. Open the URL shown by Streamlit (typically `http://localhost:8501`).

## Optional API Interface (FastAPI)

Start the API server:

```bash
uvicorn app.api:app --reload
```

Useful endpoints:

- `GET /health`
- `GET /runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/policy-history`
- `GET /audit/reviewer-events`
- `DELETE /runs/{run_id}`
- `POST /ingest-json`

`POST /ingest-json` supports optional `policy` settings to control pass/fail limits per scenario.
Each policy evaluation is stored as an immutable snapshot with an incrementing version per run.

Reviewer protection:

- `DELETE /runs/{run_id}` requires header `x-reviewer-key`
- `POST /ingest-json` with non-default `policy` requires header `x-reviewer-key`
- `GET /audit/reviewer-events` requires header `x-reviewer-key`
- `GET /admin/reviewer-auth` requires header `x-reviewer-key`
- `PATCH /admin/reviewer-auth` requires header `x-reviewer-key`
- Repeated invalid reviewer-key attempts are rate-limited with temporary lockout (`429`).

Reviewer audit trail:

- Every protected action is recorded to SQLite with action, outcome (`allowed`/`denied`), reason, actor IP, and timestamp.
- Query recent records with `GET /audit/reviewer-events?limit=100`.

Default reviewer key is `reviewer` (override with `ATL_REVIEWER_KEY`).
Streamlit reviewer mode uses `ATL_REVIEWER_CODE` (default `reviewer`).

Reviewer key rotation (API):

- `ATL_REVIEWER_KEY`: active reviewer key
- `ATL_REVIEWER_PREVIOUS_KEY`: previous key accepted only during grace window
- `ATL_REVIEWER_PREVIOUS_KEY_EXPIRES_AT`: UTC ISO timestamp (example: `2026-07-01T00:00:00Z`)

The API also stores reviewer auth settings in SQLite, so you can rotate keys through the admin endpoint without editing env files.
The Docker volume keeps those settings persistent across restarts.

During rotation, both active and previous keys are accepted until expiry.
After expiry, only the active key is valid.

Reviewer lockout controls (API):

- `ATL_REVIEWER_INVALID_LIMIT` (default `20`)
- `ATL_REVIEWER_INVALID_WINDOW_SECONDS` (default `300`)
- `ATL_REVIEWER_LOCKOUT_SECONDS` (default `120`)

Admin reviewer auth update example:

```bash
curl -X PATCH http://localhost:8001/admin/reviewer-auth \
  -H "x-reviewer-key: reviewer" \
  -H "Content-Type: application/json" \
  -d '{"active_key":"reviewer-new","previous_key":"reviewer","previous_key_expires_at":"2026-07-01T00:00:00Z"}'
```

## One-Command Docker Run (Dashboard + API)

Build and run both services with shared SQLite persistence:

```bash
docker compose up --build
```

Then open:

- Dashboard: `http://localhost:8501`
- API docs: `http://localhost:8001/docs`

Data is stored in the host-mounted `./data` directory.

Optional: override host ports if needed:

```bash
ATL_DASHBOARD_PORT=8502 ATL_API_PORT=8001 docker compose up --build
```

## Cloudflare Tunnel Deployment

This project can be exposed through Cloudflare Tunnel without changing the app code.

1. Create a tunnel in Cloudflare Zero Trust and copy the tunnel token.
2. Copy `.env.example` to `.env` and fill in `CLOUDFLARED_TUNNEL_TOKEN`.
3. Configure the hostnames in Cloudflare to route to the local compose services:
   - Dashboard -> `http://dashboard:8501`
   - API -> `http://api:8000`
4. Start the stack with the tunnel override:

```bash
CLOUDFLARED_TUNNEL_TOKEN=your-tunnel-token docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml up --build
```

5. Open the Cloudflare-hosted dashboard URL you mapped in Zero Trust.

Notes:

- The tunnel container joins the same compose network as the app services.
- The app still uses the host-mounted `./data` directory for SQLite persistence.
- The API can stay private and only be exposed if you explicitly route it through Cloudflare.

## Run Tests

```bash
pytest -q
```

## Lint and Type Check

```bash
ruff check app streamlit_app.py tests
mypy app streamlit_app.py
```

## Continuous Integration

GitHub Actions workflow is included at `.github/workflows/ci.yml`.

On every push and pull request, CI will:

- Install dependencies from `requirements.txt`
- Run Python compile checks
- Run lint checks with `ruff`
- Run type checks with `mypy`
- Run test suite with `pytest -q tests`

## Supported Input Columns

The ingestor normalizes multiple aliases and can handle partial logs. Preferred columns:

- `ts` (timestamp)
- `speed_mps`
- `steering_deg`
- `acceleration_mps2`
- `lane_id`
- `distance_to_lead_m`
- `stop_sign_detected`

Aliases like `timestamp`, `speed`, `steering`, `accel`, `lane`, and `stop_sign` are also supported.

## Project Layout

- `streamlit_app.py`: dashboard app
- `app/ingestion.py`: CSV/JSON parsing and normalization
- `app/detection.py`: event detection and scenario tagging
- `app/db.py`: SQLite schema and data access
- `app/reporting.py`: report generation and export
- `app/sample_data.py`: synthetic run generator

## Notes

- The SQLite database is created automatically as `autonomy_test_logger.db` in the project root.
- You can use the **Generate and Ingest Synthetic Sample** button to quickly test the pipeline.
