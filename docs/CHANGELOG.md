# CHANGELOG

Date: 2026-03-30
Project: `C:\Users\kiddy.nguyen\Desktop\TEST`

## Goal

Rebuild the old Excel-driven workflow into a local web app that can replace:

- `FORMAT COI REQUEST.xlsx`
- `FABRIC COTROLLING AND WRITE OFF PROJECT.xlsx`
- `S25V07971 COI.xlsx` as the live sample/reference workbook

The user requested this order of work:

1. Unlock source/auth flow first.
2. Build business UI after that.
3. Work by epic, test each epic, and record every change here.

## Epic Roadmap

- Epic 1: Unlock PPO report and GW auth/query. Status: done.
- Epic 2: Replace the old source console with business UI for COI and Fabric Left. Status: done.
- Epic 3: Validation, TODO list, changelog, and startup stability. Status: done.

## Epic 1 - Source/Auth Unlock

### Files changed

- `backend/scraper/gw_client.py`
- `backend/scraper/ppo_parser.py`
- `backend/engine/fabric_engine.py`
- `backend/app.py`

### What changed

- Rebuilt `gw_client.py` from scratch for this project.
- Added GO report fetch + parse flow based on live source URLs from the `auto` project.
- Added PPO report fetch with browser fallback.
  - Direct HTTP to PPO report may return `401`.
  - Browser fallback now waits for SSRS report text to finish rendering before parsing.
- Added GW query flow with 3 backends:
  - `headless`
  - `edge`
  - `http`
- Added PPO/GW caches to avoid re-querying the same PPO repeatedly.
- Added direct helpers used by current backend:
  - `fetch_go_color_summary`
  - `fetch_go_ppo_mapping_only`
  - `fetch_ppo_browse`
  - `fetch_tendam_ppo_status`
  - `query_gw_by_go_list`
- Fixed duplicated combo build block in `ppo_parser.py`.
- Tightened Fabric Left stock matching:
  - if stock rows contain matching `PO No` values for the GO PPO list, stock is scoped to those rows first
  - this prevents broad false-positive matching across unrelated PPOs
- Added `stock_scope` to Fabric Left output for easier validation.
- `/api/gw/query` now accepts a `backend` parameter.

### Live validation

- `fetch_go_ppo_mapping_only('S25V07971')`
  - Result: `2` PPOs
  - PPOs: `POUT25SB0007971A`, `POUT25SB0007971B`
- `fetch_ppo_fabric_combos('POUT25SB0007971A', backend='headless')`
  - Result: `ok=True`
  - Backend used: `headless`
  - Fabric lines: `60`
  - Fabric combos: `20`
- `fetch_ppo_fabric_combos('POUT25SB0007971B', backend='headless')`
  - Result: `ok=True`
  - Fabric combos: `1`
- `fetch_go_color_summary('S25V07971')`
  - Result: `21` combined PPO fabric combos
  - PPO fetch errors: `[]`
- `build_fabric_rows('S25V07971')`
  - Result: `ok=True`
  - `match_mode=ppo_combo_enriched`
  - `stock_scope=ppo_filtered`
  - `match_count=11`
- `query_gw_by_go_list(['S25V08783'], backend='headless')`
  - Result: `ok=True`
  - Backend: `headless`
  - GW rows: `18`
- `query_gw_by_go_list(['S25V07971'], backend='headless')`
  - Result: `ok=True`
  - Backend: `headless`
  - GW rows: `0`
  - Important: this is now a real zero-row result, not an auth failure

## Epic 2 - Business UI

### Files changed

- `frontend/index.html`
- `frontend/main.css`
- `frontend/app.js`

### What changed

- Removed the old JSON source console UI.
- Rebuilt the frontend as a business workspace with 3 tabs:
  - `COI Preview`
  - `Fabric Left`
  - `Source Inspector`
- Added a new top control area:
  - GO input
  - `Load COI`
  - `Load Fabric Left`
  - sample actions for COI and GW
- Added COI workspace features:
  - summary cards
  - inline editable COI cells for user-adjusted fields
  - text filter
  - CSV export
- Added Fabric Left workspace features:
  - summary cards
  - stock upload
  - reload default stock file
  - current cache inspector
  - text filter
  - CSV export
- Added Source Inspector features:
  - GO summary view
  - GW query view
  - source map view
  - raw JSON panel for backend validation
- Reworked the visual style into a real operator UI instead of a dev console.

### UI smoke tests

- Main UI smoke: `data/cache/ui_smoke_summary.txt`
  - Title loaded
  - COI stat cards: `6`
  - COI table rows: `23`
  - Source Inspector confirmed backend data for sample GW query
- Fabric UI smoke: `data/cache/ui_fabric_smoke.txt`
  - Fabric stat cards: `6`
  - Fabric table rows: `23`
  - Match mode shown as `ppo_combo_enriched`
  - Stock scope shown as `ppo_filtered`
- Screenshot artifact:
  - `data/cache/ui_smoke.png`

## Epic 3 - Startup, Changelog, Stability

### Files changed

- `backend/app.py`
- `launcher.py`
- `CHANGELOG.md`

### What changed

- Added explicit asset route:
  - `/frontend/<path:filename>`
- Added runtime path bootstrap in `backend/app.py` so this command works:
  - `py backend\app.py`
- Changed Flask startup from `debug=True` to `debug=False`
  - avoids reloader child-process confusion
  - makes smoke testing and local startup more stable
- Updated `launcher.py` to also run with `debug=False`
- Rewrote this `CHANGELOG.md` cleanly from scratch
  - removed the old broken/mojibake content
  - added epic history
  - added live test results
  - added TODO list

### Startup checks

- `python test_client` check:
  - `/` => `200`
  - `/frontend/app.js` => `200`
  - `/frontend/main.css` => `200`
- Real local server check:
  - `py backend\app.py` now starts successfully from project root
  - `/api/status` returns `ok=true`

## Current File Summary

### Backend

- `backend/app.py`
  - Flask entrypoint
  - API routes
  - asset route
  - stable script startup
- `backend/scraper/gw_client.py`
  - GO/PPO/GW/Tendam source access
  - auth fallback logic
  - browser-backed PPO/GW fetching
- `backend/scraper/ppo_parser.py`
  - PPO fabric combo parser
- `backend/engine/fabric_engine.py`
  - Fabric Left builder with PPO-aware stock scoping

### Frontend

- `frontend/index.html`
  - business page structure
- `frontend/main.css`
  - new UI styling
- `frontend/app.js`
  - frontend state, API calls, rendering, inline edit, CSV export

## TODO

- Add true save workflow for edited COI rows instead of browser-only in-memory edits.
- Add Excel export if users still need xlsx output, not only CSV.
- Refine COI business mapping fields still using placeholders:
  - `Type`
  - `JOB ORDER NO`
  - `Allocate Qty`
  - `Allocate %`
  - `fabric ETA`
  - `INV`
  - `Del date`
- Refine Fabric Left business rules from workbook notes:
  - `YY remark`
  - `Overage`
  - `Write Off`
  - `W/O KPI`
- Add a dedicated GW detail table in the UI if fabric planners want to review leftover rows without opening raw JSON.
- Add packaged desktop launcher if this project will be shared to non-technical users.

## Known Notes

- `S25V07971` now gives stable GO/PPO/PPO-combo data and a reasonable Fabric Left preview.
- `S25V08783` is the best GW validation sample at this time because it returns real GW rows.
- `S25V07971` returns zero GW rows in current live data, but auth/query flow is working.

## 2026-03-30 Review Fix Round

### User feedback that triggered this round

- Fabric Left was still merging one color across multiple PPOs.
- COI Preview did not match the requested Excel structure.
- The previous preview was still too close to a technical dump instead of the real workbook/spec layout.

### Files changed

- `backend/scraper/go_parser.py`
- `backend/scraper/ppo_parser.py`
- `backend/engine/coi_engine.py`
- `backend/engine/fabric_engine.py`
- `frontend/app.js`
- `CHANGELOG.md`

### What changed

- Re-read the 3 Excel files again and rebuilt the data shape from the workbook/spec instead of the old color-summary shortcut.
- Tightened GO parser logic:
  - parse the real `Lot No./JO #` table
  - capture `BPO Date`, `PPC Date`, `2/2` ship allowance, and lot remark
  - parse only the real `PPO Mapping` table instead of accidentally mixing in the Buyer PO table
- Extended PPO parser logic:
  - parse `Brand`
  - parse `Avg.PPO YY`
  - parse `order_qty` / `PPO PUR QTY(YDS)` per `color + fabric type`
- Rebuilt COI preview logic around row-level data:
  - source grain is now `JO + color`, then expanded by PPO fabric lines when available
  - output columns now follow the requested COI format:
    - `BRAND`
    - `GO#`
    - `PPO`
    - `Type`
    - `COLOR_CODE`
    - `COLOR_DESC`
    - `FABRIC COLOR (For piecing only)`
    - `JOB ORDER NO`
    - `- %`
    - `+%`
    - `Qty (pcs)`
    - `BUYER_PO_DEL_DATE`
    - `Marker YY`
    - `PPO YY`
    - `Actual YY#`
    - `Required Q'ty (Yds)`
    - `Rcv Q'ty (PPO)`
    - `Allocate Q'ty (Yds)`
    - `AH Allocate Q'ty (yds)`
    - `Allocate %`
    - `Remark`
- Rebuilt Fabric Left logic:
  - uploaded fabric stock is no longer grouped across multiple PPOs
  - parser grouping key now includes `PO No`
  - preview rows are now split per `PPO + color`
  - order quantity is sourced from PPO report lines instead of the old GO-color-only approximation
  - matching is now `exact-first` for combo/color, with fuzzy matching only for longer text keys
- Limited PPO queries to active PPOs tied to the current cutting jobs instead of querying every PPO reference on the GO.
- Updated the frontend table behavior:
  - COI inline editing now targets the manual columns `AH Allocate Q'ty (yds)` and `Remark`
  - Fabric Left hides internal helper columns from the main table
  - top stat cards now show `Brand`
- Restarted the local Flask server so `/api/coi/preview` and `/api/fabric-left/go` use the new code.

### Validation after fix

- Parser validation from cached GO HTML:
  - `lot 11` now resolves to `25V07971MN11`
  - `BPO Date = 11/20/2025`
  - `ship allowance = 2/2`
- PPO parser validation from cached PPO text:
  - `Brand = GIORDANO`
  - `Avg.PPO YY = 0.7143`
  - parsed `60` fabric lines for `POUT25SB0007971A`
- Live PPO source checks:
  - `fetch_ppo_fabric_combos('POUT25SB0007971A')` => `ok=True`, `60` lines
  - `fetch_ppo_fabric_combos('POUT25SB0007971B')` => `ok=True`, `1` line
- Direct backend function checks:
  - `build_coi_preview('S25V07971')` => `ok=True`, `108` rows
  - `build_fabric_rows('S25V07971')` => `ok=True`, `24` rows
- Live API checks on restarted server:
  - `POST /api/coi/preview` => `ok=True`, `108` rows, `brand=GIORDANO`
  - `POST /api/fabric-left/go` => `ok=True`, `24` rows, `brand=GIORDANO`
  - `/` => `200`
  - `/frontend/app.js` => `200`
  - `/api/status` => `200`

### Remaining gaps noted after this round

- Some COI rows still fall back to a blank `Type` and the MES PPO number when the GO lot has no direct mapped PPO fabric line.
  - This happens on carry-over or pull-fabric cases such as remarks like `pull fabric from lot#04`.
  - The row shape is now correct, but this edge-case mapping still needs a second pass if the workbook must match those cases exactly.
- `Fabric Left` still uses placeholder values for:
  - `YY# remark`
  - `Write Off`
  - `W/O KPI`
- `Lots (hidden)` is intentionally kept in the payload but hidden in the main UI table.

## 2026-07-27 SQL accuracy, preload, and security review

### Correctness

- Fixed GO shipment allowance parsing for `2/2`, `-2/+2`, `+/-2`, `+-2`, and `±2`.
- Preserved lot-level allowance when a GO Report lot has no PPO mapping; these rows no longer fall back to `0/0`.
- Distinguished a confirmed warehouse zero from an unmatched warehouse row. Unmatched rows now carry `Rcv Data Status=NOT_FOUND` and a blank received quantity.
- Removed unsafe raw-GRN and cross-fabric-type guesses that could overstate received fabric, especially after returns.
- Shipment query failures retain an expired last-known-good result and mark it stale/error instead of silently replacing it with an empty result.
- Fixed an old-browser-response race that could overwrite the UI after the operator had already selected another GO.

### SQL cache and preload

- Added the v56 source/snapshot contract and isolated it in `live_sheet_snapshot_v56.db` and `live_sheet_store_v56.db`.
- Invalid older payload versions are no longer counted as current or `READY`.
- Added one-process worker leasing, proactive warehouse/shipment polling, source-change detection, priority rebuilds, and downward/deletion correction support.
- Fixed the ETA verification selector so the poller advances through the active GO population instead of rechecking the same first batch.
- Rejects staged GO topology when `go_feed` has a newer source stamp, then refreshes lots/PPO mapping before warehouse polling or sheet rebuild.
- Stages missing/changed GO topology ahead of the historical snapshot backlog without waiting on the slow received/shipment views; volatile verification is invalidated and queued immediately afterward.
- Serializes same-GO builds and uses monotonic build tokens so a slower stale build cannot overwrite a newer result across threads or processes.
- Standby processes now retry the worker lease automatically; warm-up remains incomplete while any active GO lacks staged topology, PPO mapping, current source verification, or a current v56 snapshot.
- Normal UI sheet requests now require current source verification and fail closed rather than returning stale fabric quantities.
- Reduced duplicated SQL bundle loads and deferred optional slow enrichments during bulk snapshot builds.

### Credentials and security

- Main SQL now resolves the `longtat` login from Windows Credential Manager target `ESQ_LEFTOVER_SQL`; no SQL/GW passwords remain in source defaults.
- Shipment SQL remains on its dedicated credential because `longtat` has no login on that separate server.
- Added upload limits, local-path allowlisting, HTTPS remote-host allowlists, optional API-token authentication, response security headers, redacted diagnostics, and localhost-only default binding.
- LAN binding now requires `APP_API_TOKEN`; direct status routes expose safe aggregates only, and workbook GET routes use the same path allowlist as mutations.
- Removed CSP-blocked inline UI styles while keeping `script-src 'self'` and `style-src 'self'`.
- Removed an unused Google Maps capture containing an API key and redacted an old DHL credential from the UTF-16 VBA analysis dump. Those external credentials still require rotation by their owners.
- Added modern ODBC-driver auto-selection plus verified-TLS and fail-closed encryption settings; the installed legacy driver remains the deployment prerequisite for enabling TLS.
- Added the Waitress entry point `backend.server`; import-time worker startup and accidental duplicate workers were removed.

### Validation

- Main SQL connectivity: database `ESQ_DATA`, effective login `longtat`.
- Runtime: `/` returns `200`; snapshot and source-refresh workers are alive and hold the single-process lease.
- Runtime allowance sample `S26V06095`: two rows, both `+%=2.0` and `-%=2.0`.
- Automated tests: `39/39` passing after this review.
- Python compile and JavaScript syntax checks pass.

### Database follow-up

- The authoritative received/FOC view is the remaining latency bottleneck: approximately 16-18 seconds for a cold batch of eight PPOs in current measurements.
- Strict sub-minute refresh coverage for every active GO requires an indexed/materialized PPO-keyed read model from the database team and read-only access for `longtat`.
