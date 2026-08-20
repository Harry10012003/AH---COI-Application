from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent.parent
_dotenv_path = _project_root / ".env"
if _dotenv_path.exists():
    load_dotenv(_dotenv_path)

from waitress import serve

from backend.app import app, start_background_services, validate_bind_security


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the TEST COI application with Waitress.")
    parser.add_argument("--host", default=os.getenv("APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("APP_PORT", "5070") or "5070"))
    parser.add_argument("--threads", type=int, default=int(os.getenv("APP_THREADS", "64") or "64"))
    args = parser.parse_args()
    validate_bind_security(args.host)
    # Serve the cached UI immediately. SQL warm-up continues in background;
    # waiting here used to leave the LAN endpoint unavailable for up to 45s.
    start_background_services(wait_for_startup=False)
    serve(app, host=args.host, port=args.port, threads=max(4, args.threads))


if __name__ == "__main__":
    main()
