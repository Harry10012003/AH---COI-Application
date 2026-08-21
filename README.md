# COI Total

`COI Total` combines two authenticated workspaces in one web application:

| Workspace | Purpose |
| --- | --- |
| **COI Process** | Search a GO, review live COI data, manage allocation/edit state, and export or issue the COI workflow. |
| **Pre-COI** | Create and update the Pre-COI workbook from GO / YPD / MES data in the browser, review its two Excel sheets, then download the approved result. |

The project uses a Flask + Waitress backend and a React + Vite frontend. It is designed to run on the internal network, not as a public internet application.

## What changed in COI Total

- Added a COI landing page that routes users to **Pre-COI** or **COI Process**, with Back/Home navigation.
- Migrated the former desktop Pre-COI workflow to browser jobs and browser download. No server-side user output folder is required.
- Added Pre-COI draft review for both `COI` and `COI Collar/Cuff` sheets before final download.
- Added editable PPO and YY Req No cells, multi-line paste, keyboard arrow navigation, fill-handle drag, double-click fill-down, auto-scroll while dragging, fill preview, animated target border, multi-level Undo/Redo, and virtualization for large tables.
- Added **Update YY Req No**, **Update PPO Qty**, **Update CM**, **Download Excel**, and the current user guide in a modal.
- Restricted Pre-COI APIs and route access to the `AH` account.
- Added the Tessellation mark as the application logo and browser favicon.
- Restored the COI Process allocation rule that prioritizes rows with `CUTTING STATUS = CUTTED`; added a regression test for it.

## Architecture

```text
Browser (React + Vite)
  ├─ Login and COI menu
  ├─ COI Process: GO selector and COI workspace
  └─ Pre-COI: job progress, draft review grid, Save As download
             │
             ▼
Flask API + Waitress
  ├─ Authentication and route authorization
  ├─ COI SQL/live-sheet engine and SQLite snapshot workers
  ├─ Pre-COI job store and workbook export service
  └─ GO / PPO / GW / MES / YPD source clients
             │
             ▼
SQL Server, ESCM/YPD, GW, MES, local SQLite cache
```

Important folders:

```text
backend/
  server.py                 Only supported backend entry point
  app.py                    Flask routes, auth, COI endpoints
  sources.py                Source URLs, paths, credential configuration
  config/credentials.py     Environment / Windows Credential Manager resolver
  engine/                   COI engine, allocation, snapshots, export, issue archive
  precoi/                   Web Pre-COI jobs, parsers, Excel export, routes
  scraper/                  GO, PPO, GW, MES and sample-status clients
frontend/
  src/pages/COIHome.jsx     Menu for the two workspaces
  src/pages/COIWorkspace.jsx
  src/pages/PreCoiWorkspace.jsx
  src/pages/PreCoiDraftModal.jsx
  src/pages/PreCoiGuideModal.jsx
  public/logotes.svg        Tessellation mark and favicon source
tests/                      unittest suite and parser fixtures
assets/                     Templates and source samples
data/cache/                 Runtime SQLite/jobs/cache only; never commit
```

## Quick start

### Prerequisites

- Windows with Python 3.
- Node.js/npm for the frontend build.
- Network access to the approved SQL, ESCM/YPD, GW and MES sources.
- SQL credentials configured by environment variables or Windows Credential Manager.

### Install and run

```powershell
cd "D:\COI Merge V2\COI Total"
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

Do not run `backend/app.py` directly. `backend.server` loads `.env`, validates the bind configuration, and owns the background worker lifecycle.

### Frontend development

Run the backend as above, then in a second terminal:

```powershell
cd "D:\COI Merge V2\COI Total\frontend"
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` to Flask.

## Authentication and authorization

- Every application page requires an authenticated COI session.
- COI Process respects the normal editor/viewer permissions.
- Pre-COI is restricted twice: React redirects any non-`AH` user away from `/pre-coi`, and every `/api/precoi/*` endpoint rejects non-`AH` users with `403`.
- The Pre-COI restriction is username-based and case-insensitive (`AH`). Update `backend/precoi/routes.py` only if the business owner changes that policy.

## COI Process

### User flow

1. From Home, choose **COI Process**.
2. Search/select a GO from the GO selector.
3. Open the COI workspace to inspect source data, stock/allocation and editable COI fields.
4. Refresh or save permitted edits, then export/issue according to the established COI workflow.
5. Use Home or Change to return to GO selection.

### Data behavior

- The live-sheet engine stages SQL source data into SQLite snapshots so the UI can return cached sheets while the background worker refreshes sources.
- `Rcv Data Status = NOT_FOUND` means the receipt record is not available; it must not be interpreted as received quantity `0`.
- Stock allocation is verified from the dedicated stock source. If the source is unavailable, the application reports it instead of silently allocating against receipt data.
- Allocation order prioritizes `CUTTING STATUS = CUTTED`, then due date, required quantity/lot/JO ordering. This keeps physically cut work ahead of later rows when stock is constrained.
- A source-cache warning is distinct from an application crash. If source data is stale, wait for the SQLite preload worker or retry after the relevant source is healthy.

## Pre-COI

### Purpose

Pre-COI produces or updates the Excel workbook previously created by the desktop application, while keeping user review and final file saving in the browser.

The page works with GO, YPD and MES/ESCM source data and keeps the workbook format and two-sheet result structure:

- `COI`
- `COI Collar/Cuff`

### Standard workflow

1. Sign in as `AH` and select **Pre-COI** from Home.
2. Enter one or more GO numbers. Separators such as comma, spaces, new lines, or a batch input are accepted by the job parser.
3. Enter the user's ESCM account and password when an action needs YPD access.
4. Select **Create Output**. The page creates an asynchronous job and shows a controlled progress/log display.
5. When ready, select **Review & Input PPO**. Review both tabs, enter or paste PPO/YY values, then **Save Draft**.
6. Select **Update PPO Qty** and/or **Update YY Req No** as needed. The app runs the selected update against the saved draft.
7. After an update, review the filled values in both sheets. Select **OK & Download** only after confirming the result.
8. The browser opens a Save As dialog where supported. Choose the destination folder and save the Excel file.

For a CM-only GO, use **Update CM**. The server validates that the GO is eligible for the CM workflow.

### Pre-COI action reference

| Action | Input | Result |
| --- | --- | --- |
| **Create Output** | GO list + ESCM account/password | Builds the initial Pre-COI workbook/draft from GO, YPD and MES sources. |
| **Review & Input PPO** | Saved draft | Opens the two-tab spreadsheet review modal. |
| **Update YY Req No** | Saved draft + ESCM account/password | Refreshes Marker YY using YPD data. |
| **Update PPO Qty** | Saved draft | Updates PPO quantities from saved PPO values. |
| **Update CM** | GO list | Creates the CM workbook for an eligible CM GO. |
| **Download Excel** | Completed job | Sends the final `.xlsx` to the browser. |
| **Clear Log** | None | Clears only the on-screen job log. |

### Spreadsheet review controls

- Edit `PPO` and `YY Req No` cells directly.
- Paste multiple values into consecutive cells.
- Use `↑` and `↓` to move through a column after editing, making copy/paste review faster.
- Drag the cell fill handle to copy a value down. When the pointer reaches the bottom edge, the grid auto-scrolls.
- Double-click the fill handle to fill the value to the last row.
- See the number of target rows before releasing, for example `Fill PPO to 186 rows`.
- The active target cell is highlighted during fill so the UI visibly tracks progress.
- Undo and redo multiple fill actions using **Undo fill** and **Redo fill**.
- Tables with more than 1,000 rows use row virtualization to keep the modal responsive.
- Save the draft before running an update. Closing with unsaved changes asks for confirmation.

### Download and file naming

- The browser receives the workbook as a download; it does not write directly to a fixed server or user path.
- Browsers that support the File System Access API open a Save As picker before downloading. Otherwise the browser download behavior applies.
- The output file is named `Pre-COI <GO1>-<GO2>.xlsx`. For long GO batches, the name is shortened safely.
- Job artifacts are owned by the current authenticated user and stored only in the runtime cache for the job lifecycle. They are not source-controlled.

### ESCM password safety

- ESCM account/password are required only for YPD-dependent actions.
- If **Remember account/password** is checked, the values are stored in that browser profile's `localStorage`. This is intended only for a personal Windows/browser profile; do not enable it on a shared PC.
- The server does not commit or write those user-entered ESCM values into project source control.
- Never put real passwords in `.env.example`, README files, test fixtures, Git commits, or tickets.

## Runtime configuration

Copy the template only when environment variables are the chosen deployment method:

```powershell
copy .env.example .env
notepad .env
```

Primary SQL values use the `SQL_SERVER_*` keys. The stock source can use `STOCK_SQL_*` keys or its dedicated Windows Credential Manager target. Optional shipment source settings use the corresponding `SHIPMENT_SQL_*` keys.

For this workstation deployment, the credential resolver can obtain approved values from Windows Credential Manager (for example the main SQL and stock SQL targets) instead of storing passwords in files. Keep `.env` local; it is ignored by Git.

Useful settings:

| Setting | Purpose |
| --- | --- |
| `APP_HOST`, `APP_PORT`, `APP_THREADS` | Waitress bind and worker threads. |
| `APP_ALLOWED_ORIGINS` | Allowed frontend origins; include Vite development origins only when needed. |
| `TEST_CACHE_DIR` | Relocates SQLite cache/job data for a test or isolated run. |
| `SQL_SERVER_*` | Primary COI SQL source. |
| `STOCK_SQL_*` | Dedicated stock/allocation SQL source. |
| `SHIPMENT_SQL_*` | Optional shipment/on-way SQL source. |

## Build, test and verification

```powershell
# Backend tests
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v

# Focused Pre-COI suite
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_precoi*.py" -v

# Frontend checks
cd frontend
npm run lint
npm run build
```

The test suite uses `unittest`; it does not use pytest. Tests should not depend on a live internal SQL/ESCM system unless explicitly written as an integration test.

## Operational notes

- `data/cache/`, `data/exports/`, `output/`, build output, virtual environments and browser test artifacts are local runtime data and must not be committed.
- If a COI endpoint shows a `SQL_SOURCE_ERROR`, first distinguish a missing/stale source from an application exception. A Python stack trace with `NameError` is a code defect and must be fixed before retrying the data source.
- After changing frontend files, run `npm run build` before starting the production Flask server.
- After changing backend Python, restart `backend.server`; an already-running process still has the old code in memory.

## Changes included on the `Total-COI` branch

1. Wrapper menu and routes for the merged COI and Pre-COI workflows.
2. Full browser-based Pre-COI job, draft, workbook review, update and download flow.
3. AH-only protection for Pre-COI UI and API endpoints.
4. Spreadsheet-like PPO/YY review UX, including large-table virtualization and fill history.
5. Tessellation branding in login/header plus favicon.
6. COI allocation crash repair: restore cutting-status priority helper and regression coverage.
7. This README, describing architecture, setup, security boundaries, workflows and operational behavior.
