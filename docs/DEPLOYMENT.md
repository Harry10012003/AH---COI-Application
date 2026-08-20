# TEST deployment and credential handover

## Credentials

The application does not contain or need an AWS access key. Its only AWS-hosted
dependency is the SQL Server RDS endpoint, accessed with SQL authentication.
The main database expects the read-only `longtat` login from Windows Credential
Manager target `ESQ_LEFTOVER_SQL`.

On a new Windows workstation, ask the credential owner for the password, then
run the interactive installer included in this project:

```powershell
.\scripts\Install-SqlCredential.ps1 -Target ESQ_LEFTOVER_SQL -UserName longtat
```

The password is entered securely and is not written to the repository. Confirm
the setup without exposing it:

```powershell
py -3 -c "from backend.sources import get_source_map; print(get_source_map()['sql_live']['credentials']['configured'])"
```

`TEST_SHIPMENT_SQL` and `TEST_GW_LOGIN` are separate optional credential
targets. Do not reuse the main SQL password for them. `.env.example` is a
reference only: this application intentionally does not load `.env` files at
runtime. Configure non-secret values in the Windows service/process
environment and keep passwords in Credential Manager.

Install Microsoft ODBC Driver 18, then use the TLS values in `.env.example`.
The legacy Windows `SQL Server` driver cannot validate the current RDS
certificate, so it must not be used for a production handover that requires
encrypted transport.

Microsoft Edge is also required for the browser-backed GW and MES sources. If
the server does not have Edge available, install the Playwright Chromium
runtime after installing Python packages:

```powershell
py -m playwright install chromium
```

## Start

```powershell
py -m pip install -r requirements.txt
py -m backend.server --host 127.0.0.1 --port 5070 --threads 24
```

For the trusted company LAN, use `START_LAN.bat`. It deliberately enables the
private-network bypass and cross-origin read access for the Cutting COI feed.
This TEST deployment intentionally does not require an API token because it
is operated only on the trusted company LAN. Do not expose this service to a
public or untrusted network without adding an authenticated gateway in front
of it.

Use `start_TEST_with_status.bat` for the normal LAN/status workflow. It now
uses the production Waitress entry point and does not start Flask's development
reloader, which could otherwise create duplicate SQL worker processes. The
service starts immediately from SQLite while SQL warm-up continues in the
background.

The service account needs write access to `data/cache` (or to the directory
configured through `TEST_CACHE_DIR`) and to `ONEDRIVE_COI_FOLDER_PATH` when
users issue COI workbooks. A clean IT ZIP intentionally starts with an empty
cache; source rows are reloaded from internal SQL after startup.

For a persistent server installation, configure this command as a Windows
service or Scheduled Task with the project folder as its working directory:

```powershell
py -m backend.server --host 0.0.0.0 --port 5070 --threads 24
```

Run it only on the trusted company network. The `0.0.0.0` binding deliberately
has no API-token requirement for the internal workflow; an internet-facing
deployment requires an authenticated reverse proxy or gateway managed by IT.

## Health checks

```text
GET /api/status
GET /api/sql/status
GET /api/sql/preload/status
GET /api/sql/source-cache/status
GET /api/cutting/coi/latest?limit=5000
```

The first four routes reveal only safe aggregate status unless
`APP_DIAGNOSTICS_DETAIL=true` is explicitly set on a trusted local machine.

## PPO edit synchronization

Editing a `PPO` cell now performs this sequence synchronously:

1. Save the operator override in SQLite.
2. Query SQL Server with a targeted lookup for the resulting PPO/fabric quantities.
3. Return the recalculated sheet to the UI.
4. If that GO was already issued, replace the current rows in the SQLite
   Cutting feed, update the issued workbook in the OneDrive COI folder, and
   rebuild `COI-CUTTING-COMBINED.xlsx`.

The original issue batch and `ISSUE AT` stay unchanged. `LAST SYNC AT` and the
feed `data_version` identify the later correction. A GO that has not yet been
issued is never auto-issued merely because an operator edits it.
