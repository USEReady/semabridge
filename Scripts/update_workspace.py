#!/usr/bin/env python
"""Update stored Fabric workspace ID in the credential manager."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _default_db_url() -> str:
    db_path = Path.home() / ".semabridge" / "semabridge_state.db"
    return f"duckdb:///{db_path}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update the saved Fabric workspace_id credential."
    )
    parser.add_argument(
        "workspace_id",
        nargs="?",
        default=os.getenv("FABRIC_WORKSPACE_ID"),
        help="Fabric workspace GUID (or set FABRIC_WORKSPACE_ID env var).",
    )
    parser.add_argument(
        "--db-url",
        default=_default_db_url(),
        help="Credential DB URL override (default: ~/.semabridge/semabridge_state.db).",
    )
    args = parser.parse_args()

    if not args.workspace_id:
        parser.error("workspace_id is required (arg or FABRIC_WORKSPACE_ID env var).")

    repo_root = Path(__file__).resolve().parent.parent
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    from semabridge.repository.credential_manager import CredentialManager

    try:
        cm = CredentialManager(url_override=args.db_url)

        creds = cm.get_credentials("fabric", mask_secrets=False)
        current_workspace_id = creds.get("workspace_id", "NOT SET")
        print(f"Current workspace_id: {current_workspace_id}")

        cm.save_credentials("fabric", {"workspace_id": args.workspace_id})

        updated_creds = cm.get_credentials("fabric", mask_secrets=False)
        print(f"Updated workspace_id: {updated_creds.get('workspace_id')}")
        return 0
    except Exception as exc:
        print(f"Error updating workspace_id: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
