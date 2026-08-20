# AGENTS.md — TEST COI Application

## Project overview
- Python 3 Flask + Waitress backend, Vite + React frontend.
- Backend: `backend/` (Flask routes, SQL engines, scrapers). Frontend: `frontend/` (React SPA).
- No .sln/.csproj — this is a **Python project**, not .NET.

## Start & run

**Backend:**
```powershell
.\venv\Scripts\Activate.ps1
py -m backend.server --host 127.0.0.1 --port 5070 --threads 24
```

**Frontend dev (separate terminal):**
```powershell
cd frontend
npm install
npm run dev
```
Vite dev server at `http://localhost:5173`, proxies `/api` to Flask backend.

**Production:**
```powershell
cd frontend; npm run build
# Flask serves frontend/dist/ — open http://127.0.0.1:5070
```

- **Never run `backend/app.py` directly** — only `backend/server.py` loads `.env` and owns the background worker lock.

## Tests
```powershell
py -m unittest discover tests
```
- All tests use `unittest` (no pytest). Run from the project root.
- Tests require `pywin32` for credential tests.

## Environment & credentials
- `backend/server.py` loads `.env` at startup via `python-dotenv`.
- Passwords in `.env`: `SQL_SERVER_USER` + `SQL_SERVER_PASSWORD`, `SHIPMENT_SQL_SERVER_USER` + `SHIPMENT_SQL_SERVER_PASSWORD`, `GW_LOGIN_USER` + `GW_LOGIN_PASSWORD`.
- A partial env override (username only, or password only) is **rejected**.
- Install [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server?view=sql-server-ver17).

## Frontend architecture
- **Pages**: `/` (GO selector), `/coi?go=...`; Material Status and Checking Line are disabled/removed
- React Router v6, Vite build, proxy `/api` to Flask in dev
- Design system: primary `#0096BE`, Inter font, CSS variables in `src/index.css`

## Backend architecture
- `backend/sources.py` — central config (URLs, credentials, paths)
- `backend/config/credentials.py` — credential resolution
- `backend/engine/` — COI, SQLite cache, issue archive, workbook logic
- `backend/scraper/` — GO, PPO, GW, MES source clients
- SQLite caches: `live_sheet_snapshot_v56.db`, `live_sheet_store_v56.db`, `issued_coi_archive_v1.db`
- Background workers start once via `start_background_services()`
- `Rcv Data Status=NOT_FOUND` ≠ received qty = 0

## Key conventions
- `__future__ import annotations` project-wide
- `pathlib.Path` preferred over `os.path`
- No `pytest` — use `unittest` only
- `data/cache/` gitignored
- Excel templates in `assets/templates/` loaded by `sources.py`
