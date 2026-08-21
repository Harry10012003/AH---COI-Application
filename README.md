# COI Total

`COI Total` is the combined COI web application. It has one authenticated COI menu with two workspaces:

- **Pre-COI**: creates and updates `COI Master.xlsx` through browser upload/download.
- **COI Process**: the existing GO selector and COI workspace.

## Run locally

```powershell
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
py -m backend.server --host 127.0.0.1 --port 5070
```

Open `http://127.0.0.1:5070`.

## Pre-COI web behavior

- Browser users upload an existing `.xlsx` for **Update YY Req No** and **Update PPO Qty From Excel**.
- Finished files are downloaded by the browser; the server keeps each temporary job artifact only briefly and never returns a local path.
- YPD credentials are transient for the current job. The web app does not persist a YPD password.
- Configure SQL credentials through the existing `.env`/Windows Credential Manager flow. Do not put credentials in `backend/precoi/clients.py`.

# Existing COI Process documentation

Web app thay thế Excel workflow cho COI preview, Fabric Left matching, và source validation (GO, PPO, GW, MES).

## Quick Start

```powershell
# 1. Tạo venv + cài dependencies
py -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt

# 2. Cấu hình .env
copy .env.example .env
notepad .env

# 3. Build frontend
cd frontend
npm install
npm run build
cd ..

# 4. Chạy backend
.\venv\Scripts\Activate.ps1
py -m backend.server --host 127.0.0.1 --port 5070
```

Mở `http://127.0.0.1:5070`.

## Login và quyền

| Username | Role | Quyền |
|----------|------|-------|
| `AH` | Editor | Search, xem, edit, refresh PPO, export và Issue COI |
| `Viewer` | Viewer | Search GO và xem COI snapshot ở chế độ read-only |

Hai account dùng initial password nội bộ đã được bàn giao cho stakeholder. Backend lưu password hash và cấp session token có thời hạn; restart backend sẽ yêu cầu đăng nhập lại.

## Dev mode (2 terminals)

```powershell
# Terminal 1 — Backend
.\venv\Scripts\Activate.ps1
py -m backend.server --host 127.0.0.1 --port 5070

# Terminal 2 — Frontend (hot reload)
cd frontend
npm run dev
```

Mở `http://localhost:5173` (Vite dev server, proxy `/api` → Flask).

## Tests

```powershell
.\venv\Scripts\Activate.ps1
py -m unittest discover tests
```

## Cấu hình `.env`

Điền ít nhất:

```env
SQL_SERVER_USER=YOUR_SQL_USER
SQL_SERVER_PASSWORD=YOUR_SQL_PASSWORD
SQL_SERVER_HOST=esq-mssql-std-dm.cogfagymhkon.ap-southeast-2.rds.amazonaws.com
SQL_SERVER_DATABASE=ESQ_DATA

# Stock allocation source (separate credential target; never reuse implicitly)
STOCK_SQL_SERVER=esq-mssql-std-dm.cogfagymhkon.ap-southeast-2.rds.amazonaws.com
STOCK_SQL_DATABASE=ESCM_EGV_EAV
STOCK_SQL_SCHEMA=invsubmat
STOCK_SQL_VIEW=V_Inv_Stock_EGV_EAV
STOCK_SQL_CREDENTIAL_TARGET=COI_STOCK_SQL
```

## Architecture

```
backend/
  server.py              Waitress entry point, loads .env
  app.py                 Flask routes & security
  sources.py             Central config (URLs, credentials, paths)
  config/credentials.py  Credential resolution
  engine/                COI, SQLite cache, issue archive, workbook logic
  scraper/               GO, PPO, GW, MES source clients
frontend/
  src/                   React SPA (Vite + React Router)
    pages/               Login, GOSelector, COIWorkspace
    api.js               API layer
    index.css            Design system
  dist/                  Build output → Flask serves this
data/cache/              SQLite stores (gitignored)
```

## Pages

| Route | Page |
|-------|------|
| `/login` | Stakeholder login |
| `/` | GO selector — search & pick GO |
| `/coi?go=...` | COI Workspace — preview, edit, issue, export |

## Design system

- Primary: `#0096BE`, Background: `#F7F9FB`, Cards: white
- Font: Inter, system-ui
- Pastel status colors, rounded corners (8-16px), shadow scale

## Notes

- **Không chạy `backend/app.py` trực tiếp** — `server.py` loads `.env` + owns worker lock
- SQL data staged in `data/cache/live_sheet_snapshot_v56.db` và `live_sheet_store_v56.db`
- Background workers start once, stage GO topology từ SQL Server → SQLite
- `pymssql` + `DBUtils` connection pool (20 max), `?` → `%s` auto-convert
- Set `SQL_SERVER_DRIVER=pymssql` and `SHIPMENT_SQL_SERVER_DRIVER=pymssql`; ODBC driver names are not used by this runtime.
- Stock allocation uses the dedicated `STOCK_SQL_*` RDS source. Configure `STOCK_SQL_USER/PASSWORD` or the `COI_STOCK_SQL` Credential Manager target; without it, stock remains `SQL_UNAVAILABLE` and allocation is not verified.
- MES Actual Cutting is no longer part of the COI payload or Issue COI gate; `SAMPLE STATUS` remains a separate enrichment.
- `Rcv Data Status=NOT_FOUND` ≠ received qty = 0
- Set `TEST_CACHE_DIR` để relocate cache
- `APP_ALLOWED_ORIGINS` cho phép UI dev (mặc định `localhost:5173` và `127.0.0.1:5173`) gọi API qua Vite proxy; origin khác vẫn bị chặn.
