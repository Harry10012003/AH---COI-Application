# TEST project layout

```text
backend/
  app.py                 HTTP boundary, validation and routes
  server.py              Waitress production entry point
  sources.py             source endpoints and non-secret configuration
  config/credentials.py  environment / Windows Credential Manager resolver
  engine/                COI, SQLite cache, issue archive and workbook logic
  scraper/               GO, PPO, GW and MES source clients
frontend/
  index.html             UI shell
  main.css               UI styles
  app.js                 UI state and API interaction
tests/                   backend contract and security tests
scripts/                 operational/audit helpers
docs/                    architecture and deployment handover documents
assets/
  templates/             COI and Fabric Left workbook templates used at runtime
  samples/               reference workbooks used for validation
  reference/             legacy reference material, not loaded by the app
data/cache/              runtime SQLite and cache files; excluded from Git and IT ZIP
```

The separate `leftover_sql_excel` legacy workbook automation, build artifacts,
logs, caches and browser/PDF artefacts are not part of the TEST web-service
deployment and are excluded from the clean IT package.

The runtime data flow is:

```text
UI -> Flask/Waitress -> SQL source cache + live SQL query -> live sheet SQLite
                                                |
ISSUE COI -> issued workbook + issue archive SQLite -> Cutting JSON endpoint
```

Only one process may own the SQL preload worker lock. Start the service through
`backend.server`/the supplied batch files; do not run `backend/app.py` directly
for normal operations.
