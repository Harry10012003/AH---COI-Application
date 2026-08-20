# COI Performance and Lean Refactor Design

Date: 2026-08-20

## Objective

Make the COI application refresh useful data quickly, remain responsive during source outages, and reduce the maintenance cost of the current monolithic SQL engine without changing upstream databases.

Success means:

- A clean cache reaches a usable state quickly instead of waiting for every GO.
- Opening or searching a GO is not blocked by the full background backlog.
- SQL calls are bounded, measurable, deduplicated, and isolated by source.
- Valid last-known-good data is never replaced by a timeout or empty failed result.
- `backend/engine/sql_live_engine.py` is reduced into focused modules behind compatible public APIs.
- Obsolete Material Status, Checking Line, and MES Actual Cutting runtime code is removed after reference and contract checks.

## Hard Boundary: Source Databases Are Read-Only

The application may issue parameterized `SELECT` statements only against source databases.

It must not execute `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `ALTER`, `CREATE`, `DROP`, `TRUNCATE`, stored-procedure writes, index changes, collation changes, or deployment scripts against:

- `ESQ_DATA`
- `ESCM_EGV_EAV`
- `EsquelRptDB`
- `EsquelEAVRptDB`
- any other upstream SQL Server database

Schema migrations and indexes are permitted only in local application-owned SQLite files under `data/cache/`. DBA recommendations remain documentation and are never executed by the app.

## Current Findings

- `backend/engine/sql_live_engine.py` is approximately 553 KB and combines connection pools, source queries, cache schema, worker scheduling, snapshot building, allocation, status mapping, and legacy compatibility.
- A clean refresh currently discovers roughly 2,898 GO records but initially produces no complete snapshots because topology and enrichment are processed through a large sequential backlog.
- Basic SQL connectivity works, while view-level calls can be slow or intermittently time out.
- The new RDS stock view is reachable and usable, but it does not expose a size column; the adapter intentionally normalizes `size_code` to an empty value.
- Worker state is partly in process memory and partly in SQLite, which makes status, retry, and duplicate-work behavior harder to reason about.
- Actual Cutting functions and compatibility fields remain in the engine even though the product no longer uses Actual Cutting as a COI gate.

## Recommended Architecture

Retain Flask, Waitress, `pymssql`, SQLite, the current API contracts, and the current frontend. Improve the system incrementally through a compatibility facade rather than a rewrite.

### 1. Measured source adapters

Create focused read-only adapters:

- `backend/engine/sql_sources/main.py`: GO, color, lot, JO, PPO, BOM, and RCV queries from `ESQ_DATA`.
- `backend/engine/sql_sources/stock.py`: RDS stock queries from `ESCM_EGV_EAV`.
- `backend/engine/sql_sources/shipment.py`: EGV/EAV shipment queries.
- `backend/engine/sql_sources/metrics.py`: duration, rows, timeout/error classification, and safe source identifiers.

Each adapter returns a common result envelope containing source key, status, rows, duration, checked time, and a stable reason code. Raw credentials and stack traces never enter UI payloads.

### 2. Two-lane refresh scheduler

Replace the single logical backlog with two lanes:

- Interactive lane: GO requested by the UI, recent changed GO, and explicitly refreshed GO.
- Background lane: remaining active GO ordered by modification time and cache state.

Use a bounded in-memory priority queue backed by SQLite state. Deduplicate GO keys before execution. A GO may have only one topology refresh and one snapshot build in flight. Interactive work may move ahead of background work but cannot create unbounded threads.

Default concurrency remains conservative because upstream views are expensive:

- Main SQL topology: 1 concurrent query group.
- Stock RDS: 2 concurrent batches.
- Shipment: 2 concurrent batches across separate databases.
- SQLite writer: 1 serialized writer transaction.

These are application limits only and are configurable. Increasing them requires measured evidence that timeout rate does not rise.

### 3. Incremental, batched source refresh

GO feed:

- Keep keyset pagination; avoid functions around indexed date columns.
- Use a fixed overlap window for recent changes.
- Record page duration and continuation key so a timeout retries only the failed page.
- Treat rows with missing modification dates through a separate bounded create-date query rather than an `OR` predicate that forces a scan.

Topology:

- Query one GO at a time because existing views are GO-oriented.
- Save a topology bundle atomically only when mandatory source reads succeed.
- Do not refresh topology again until its source version is stale or the GO feed modification stamp changes.

PPO, RCV, stock, and shipment:

- Gather PPOs for a bounded set of verified GO records.
- Deduplicate PPOs and query configurable chunks.
- Persist each successful source independently.
- Retry only failed chunks with exponential backoff and jitter.
- Never delete last-known-good rows for a source whose query failed.
- A successful empty result is authoritative only when the query completed and its scope is explicit.

### 4. SQLite local state and write efficiency

Introduce local schema metadata and indexes only in application SQLite:

- Cache schema version and migration journal.
- Queue table with GO, lane, reason, attempt count, next attempt, lease owner, and lease expiry.
- Query metrics table with source, scope hash, duration, row count, outcome, and timestamp.
- Index queue scheduling fields, source sync lookup keys, GO/PPO relationship keys, and snapshot refresh fields.

Use one transaction per logical batch instead of commits inside row loops. Prepare normalized rows before acquiring the SQLite write lock. Enable WAL and a bounded busy timeout. Run integrity checks during startup diagnostics, not on every request.

### 5. Snapshot build separation

Split snapshot assembly from source retrieval:

- Source adapters fetch and normalize raw data.
- Cache repositories store and retrieve normalized source bundles.
- `coi_snapshot_builder` is a deterministic transformation from cached source bundle plus UI allocation state to COI payload.
- Request handlers first return a compatible last-known-good snapshot and queue a refresh when stale.
- A newly requested uncached GO enters the interactive lane and receives a stable waiting response until mandatory topology is available.

Snapshot publication is atomic. A build that throws, is empty because of source failure, or fails contract validation cannot replace a valid snapshot.

### 6. Lean-code migration

Keep `sql_live_engine.py` as a facade during migration so Flask routes and tests do not change all at once. Move code in this order:

1. SQL connection pools and measured query execution.
2. GO feed and scheduler.
3. Stock, RCV, shipment, and PPO repositories.
4. SQLite schema/repositories.
5. Snapshot builder and mapping helpers.
6. Status and diagnostics.

After each move, the old internal function delegates to the new module until callers and tests have migrated. Delete the delegate only after `rg` confirms no callers and the full suite passes.

Remove obsolete code after contract checks:

- Actual Cutting cache loaders, merge helpers, payload fields, endpoints, configs, and tests that assert old behavior.
- Material Status runtime code and routes if no enabled route imports them.
- Checking Line runtime code and routes if no enabled route imports them.
- Dead compatibility parameters such as `prefer_actual_cutting_cache` and `allow_live_actual_cutting` after all callers are migrated.

`SAMPLE STATUS` remains because it uses a different MES source and is still part of COI behavior.

## Runtime Data Flow

```text
UI request / recent-change poll
            |
            v
  deduplicated two-lane queue
            |
            v
 read-only source adapters -----> safe timing/error metrics
            |
            v
 atomic SQLite source bundles
            |
            v
 deterministic snapshot builder
            |
            v
 atomic last-known-good snapshot -> API / export / issue archive
```

## Failure Behavior

- Connection failure: classify the affected source as `SQL_UNAVAILABLE`; retain its last-known-good rows and schedule backoff.
- Query timeout: fail only that source chunk; do not restart the whole GO or batch.
- Collation/binding error: classify as `SOURCE_BLOCKED` with a DBA reason code; do not attempt remediation.
- Successful zero rows: save `NOT_FOUND` only for the exact source scope that completed successfully.
- SQLite busy/error: retry the local write with a short bounded delay; source reads are not repeated solely because a local write was busy.
- Process restart: expired queue leases become runnable; completed bundles and snapshots remain reusable.

## Observability

Extend safe status diagnostics with:

- backlog counts by lane and state
- in-flight GO and source
- p50/p95 query duration per source
- successful, empty, timeout, blocked, and unavailable counts
- retry time and attempt count
- cache age and last-known-good timestamp
- snapshot build duration and validation failures

No SQL text containing values, usernames, passwords, connection strings, or raw exceptions is exposed by the API.

## Delivery Stages

### Stage A: Baseline and safety tests

- Add repeatable local measurements for clean startup, one interactive GO, recent feed, and source batch refresh.
- Add a guard test that rejects non-`SELECT` upstream SQL execution paths.
- Capture current query counts, durations, cache writes, and error rates.

### Stage B: Immediate performance fixes

- Deduplicate priority work.
- Separate interactive and background queues.
- Add bounded per-source concurrency and chunk retry.
- Batch SQLite writes and add local indexes.
- Make recent/full feed continuation resumable.

### Stage C: Module extraction

- Introduce adapters, repositories, scheduler, metrics, and snapshot builder modules behind the existing facade.
- Move tests with each responsibility while preserving public contracts.

### Stage D: Legacy removal

- Remove confirmed-dead Actual Cutting, Material Status, and Checking Line code.
- Remove unused imports, constants, tables, endpoints, frontend calls, and compatibility parameters.
- Keep a cache migration path and rollback backup.

### Stage E: Production verification

- Run a clean-cache refresh while the previous cache remains recoverable.
- Verify interactive GO priority during background warmup.
- Compare before/after duration, query count, timeout count, and snapshot throughput.

## Verification

Backend:

```powershell
.\venv\Scripts\python.exe -m unittest discover tests
```

Frontend:

```powershell
cd frontend
npm run lint
npm run build
```

Operational checks:

- `/api/health`
- `/api/sql/status`
- `/api/sql/preload/status`
- GO search during cold start
- uncached interactive GO during a large background backlog
- cached GO while each source is independently unavailable
- successful empty stock/RCV/shipment scopes
- one failed PPO batch while other batches complete
- SQLite integrity check and restart recovery
- export and Issue COI without Actual Cutting

## Acceptance Criteria

- No upstream database mutation statements exist in application execution paths.
- An interactive GO is processed ahead of the background backlog without spawning an unbounded thread.
- Repeated requests for one GO create one in-flight refresh.
- A failed source chunk does not discard valid cached data or retry successful chunks.
- Status identifies the exact source class and retry state without secrets.
- A clean cache becomes searchable after feed sync and progressively becomes COI-ready.
- Existing API payloads remain compatible except for already-approved removal of Actual Cutting fields.
- Full backend tests, frontend lint, and frontend production build pass.
- The monolithic engine is reduced through tested extraction; no broad rewrite is used.

## Rollback

- Retain pre-migration SQLite files as timestamped backups.
- Each extraction keeps a compatibility facade until verification passes.
- Local SQLite migrations are additive first; destructive cleanup occurs only after a release has successfully used the new schema.
- Reverting application code restores the old facade behavior without any upstream DB rollback because upstream DBs are never changed.
