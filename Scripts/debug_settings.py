#!/usr/bin/env python
"""Dump connector settings and related environment values for debugging."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _mask_value(key: str, value: object) -> object:
    key_lc = key.lower()
    if any(token in key_lc for token in ("pass", "secret", "token", "key")):
        return "*******"
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug connector settings and credentials.")
    parser.add_argument(
        "--connector",
        default="snowflake",
        help="Connector key in credential manager (default: snowflake).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    from semabridge.core.settings import reload_settings
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()

    try:
        cm.inject_credentials_to_env(args.connector)
    except Exception as exc:
        print(f"Warning: failed injecting {args.connector} creds into env: {exc}")

    settings = reload_settings()

    print("SETTINGS:")
    try:
        connector_settings = getattr(settings, args.connector)
        print(
            json.dumps(
                connector_settings.model_dump(exclude_unset=False),
                indent=2,
                default=str,
            )
        )
    except Exception as exc:
        print(f"Could not render settings for '{args.connector}': {exc}")

    print("\nDUCKDB CREDENTIALS:")
    all_creds = cm.get_all_credentials()
    connector_creds = all_creds.get(args.connector, {})
    if not connector_creds:
        print(f"  No credentials found for '{args.connector}'.")
    else:
        for key, value in connector_creds.items():
            print(f"  {key}: {_mask_value(key, value)}")

    print("\nOS ENVIRONMENT:")
    prefixes = (args.connector.upper() + "_", "SEMABRIDGE_")
    for key, value in sorted(os.environ.items()):
        if key.startswith(prefixes):
            print(f"  {key}={_mask_value(key, value)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
