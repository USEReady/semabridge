"""Process launcher for platforms that run source code (Databricks Apps).

Binds the API (which also serves the built web app) to the port the platform
assigns: ``DATABRICKS_APP_PORT``, else ``API_PORT``, else 8000.
"""
from __future__ import annotations

import os


def port() -> int:
    for name in ("DATABRICKS_APP_PORT", "API_PORT"):
        raw = os.environ.get(name)
        if raw and raw.strip().isdigit():
            return int(raw)
    return 8000


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    uvicorn.run(
        "semabridge.api.main:app",
        host="0.0.0.0",
        port=port(),
        workers=int(os.environ.get("SEMABRIDGE_API_PROCESSES", "1") or 1),
        proxy_headers=True,
        forwarded_allow_ips="*",
        server_header=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
