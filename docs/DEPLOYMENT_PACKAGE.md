# Clean IT deployment package

The `TEST_COI_IT_DEPLOY_*.zip` package is deliberately source-only. It can be
given to IT without copying live COI data or secrets from a developer machine.

Included:

- `backend`, `frontend`, runtime workbook templates, tests and operational scripts;
- deployment/architecture documentation and `.env.example` placeholders;
- `DEPLOYMENT_MANIFEST.txt`, generated during packaging, listing every file in
  the archive.

Excluded:

- SQL/GW passwords, local `.env` files and Windows Credential Manager entries;
- the  runtime `data/cache` SQLite databases, issued COI archive and cached GO
  responses;
- exports, logs, Python virtual environments, test/browser artefacts and Git
  metadata;
- `leftover_sql_excel`, which is a separate legacy Excel automation tool and
  not part of the TEST web-service workflow.

The application recreates the cache database on first start and refreshes data
directly from the internal SQL sources. If historical issued COI data must be
migrated, IT should receive it through an approved, separate backup process;
it is not embedded in the source ZIP.

## Build a package

From the project root, run:

```powershell
.\scripts\Build-IT-DeploymentPackage.ps1
```

The script creates a timestamped ZIP on the current user's Desktop and prints
its SHA-256 checksum. It packages through a temporary staging folder and never
deletes files from the project workspace.

## Deploy

1. Extract the ZIP into a local folder that the service account can write to.
2. Install Python 3.11+ and Microsoft ODBC Driver 18 for SQL Server.
3. Run `py -m pip install -r requirements.txt`.
4. Create the `ESQ_LEFTOVER_SQL` Windows Credential Manager entry using
   `scripts\Install-SqlCredential.ps1`; obtain the password through the normal
   credential owner, not by putting it in an environment file.
5. Configure the server-specific OneDrive destination and other optional
   variables through the service/process environment as needed. Use
   `.env.example` only as a reference.
6. Start with `start_TEST_with_status.bat` or run
   `py -m backend.server --host 0.0.0.0 --port 5070 --threads 24`.
7. Verify `/api/status`, `/api/sql/status` and
   `/api/cutting/coi/latest?limit=5000` from the company network.
