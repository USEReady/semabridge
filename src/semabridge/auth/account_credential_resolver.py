"""
Account-scoped credential resolution for SemaBridge pipelines.

This module provides the ``scoped_account_env()`` context manager which
resolves credentials from a linked ``Account`` row and injects them into
``os.environ`` for the duration of a pipeline execution. On exit, the
original environment is restored — preventing credential bleed between
concurrent multi-user requests.

Usage::

    from semabridge.auth.account_credential_resolver import scoped_account_env

    with scoped_account_env(account, db) as token:
        # os.environ now contains the account's credentials
        extractor = FabricExtractor(settings.fabric)
        ...
    # os.environ is restored to its original state

The context manager automatically calls ``ensure_valid_token()`` to refresh
expired tokens before injecting them.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Dict, Generator, Optional

from sqlalchemy.orm import Session

from semabridge.auth.encryption import decrypt_token
from semabridge.auth.token_refresher import TokenExpiredError, ensure_valid_token
from semabridge.repository.orm.models import Account
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# Maps (connector_type, credential_key) → environment variable name.
# These match the env vars that connectors already read from.
_CONNECTOR_ENV_MAP: Dict[str, Dict[str, str]] = {
    "FABRIC": {
        "access_token": "FABRIC_ACCESS_TOKEN",
        "refresh_token": "FABRIC_REFRESH_TOKEN",
        "tenant_id": "FABRIC_TENANT_ID",
        "client_id": "FABRIC_CLIENT_ID",
        "client_secret": "FABRIC_CLIENT_SECRET",
        "workspace_id": "FABRIC_WORKSPACE_ID",
    },
    "SNOWFLAKE": {
        "account": "SNOWFLAKE_ACCOUNT",
        "user": "SNOWFLAKE_USER",
        "password": "SNOWFLAKE_PASSWORD",
        "auth_type": "SNOWFLAKE_AUTH_TYPE",
        "private_key": "SNOWFLAKE_PRIVATE_KEY",
        "oauth_client_id": "SNOWFLAKE_OAUTH_CLIENT_ID",
        "oauth_client_secret": "SNOWFLAKE_OAUTH_CLIENT_SECRET",
        "oauth_token_endpoint": "SNOWFLAKE_OAUTH_TOKEN_ENDPOINT",
        "oauth_scope": "SNOWFLAKE_OAUTH_SCOPE",
        "warehouse": "SNOWFLAKE_WAREHOUSE",
        "database": "SNOWFLAKE_DATABASE",
        "schema_name": "SNOWFLAKE_SCHEMA",
        "role": "SNOWFLAKE_ROLE",
    },
    "DATABRICKS": {
        "host": "DATABRICKS_HOST",
        "access_token": "DATABRICKS_TOKEN",
        "token": "DATABRICKS_TOKEN",
        "auth_type": "DATABRICKS_AUTH_TYPE",
        "client_id": "DATABRICKS_CLIENT_ID",
        "client_secret": "DATABRICKS_CLIENT_SECRET",
        "warehouse_id": "DATABRICKS_WAREHOUSE_ID",
        "catalog": "DATABRICKS_CATALOG",
        "schema_name": "DATABRICKS_SCHEMA",
    },
}


def _load_account_credentials(
    account: Account,
    db: Session,
) -> Dict[str, str]:
    """Build a dict of env-var-name → value from the Account and its related
    global credential store.

    Resolution order:
    1. Decrypt ``Account.encrypted_token`` as JSON credential bundle (new format).
       If it contains full connection params, use them directly.
    2. Fall back to global ``CredentialManager`` for legacy Account rows that
       only have a simple access token.

    The Account's token is always used as the access token override.

    Args:
        account: The Account row.
        db: Active session.

    Returns:
        Dict mapping environment variable names to their values.
    """
    connector = account.connector_type.upper()
    env_map = _CONNECTOR_ENV_MAP.get(connector, {})
    result: Dict[str, str] = {}

    # 1. Try to load full credential bundle from Account.encrypted_token
    has_full_bundle = False
    if account.encrypted_token:
        try:
            import json
            decrypted = decrypt_token(account.encrypted_token)
            bundle = json.loads(decrypted)

            if isinstance(bundle, dict) and len(bundle) > 1:
                # This is a full credential bundle — map keys to env vars
                for key, value in bundle.items():
                    env_var = env_map.get(key)
                    if env_var and value:
                        result[env_var] = str(value)
                        has_full_bundle = True

                if has_full_bundle:
                    logger.info(
                        "Loaded full credential bundle for %s/%s (%d env vars)",
                        connector, account.tag, len(result),
                    )
        except (json.JSONDecodeError, Exception) as exc:
            logger.debug(
                "encrypted_token for %s/%s is not a JSON bundle: %s",
                connector, account.tag, exc,
            )

    # 2. Fall back to global CredentialManager for legacy accounts
    if not has_full_bundle:
        try:
            from semabridge.repository.credential_manager import CredentialManager
            cm = CredentialManager()
            service = connector.lower()
            stored = cm.get_credentials(service)

            for key, value in stored.items():
                env_var = env_map.get(key)
                if env_var and value:
                    result[env_var] = str(value)
        except Exception as exc:
            logger.debug("Could not load base credentials for %s: %s", connector, exc)

    # 3. Override with Account-specific token (always takes precedence)
    try:
        valid_token = ensure_valid_token(account, db)
        token_env = env_map.get("access_token")
        if token_env and valid_token:
            result[token_env] = valid_token
    except TokenExpiredError:
        # Let it propagate — the caller should handle re-auth prompts
        raise
    except Exception as exc:
        logger.warning("Token resolution failed for account %s: %s", account.id, exc)

    # 4. Override with account identity email (for connectors that need it)
    if account.identity_email:
        if connector == "FABRIC":
            result["FABRIC_ACCOUNT_USERNAME"] = account.identity_email
        elif connector == "DATABRICKS":
            result["DATABRICKS_ACCOUNT_USERNAME"] = account.identity_email
        elif connector == "SNOWFLAKE":
            result["SNOWFLAKE_USER"] = account.identity_email

    return result


@contextmanager
def scoped_account_env(
    account: Account,
    db: Session,
) -> Generator[str, None, None]:
    """Context manager that injects account credentials into ``os.environ``.

    .. warning::

        **CLI use only.**  This function mutates ``os.environ`` which is a
        process-global dict shared by all threads.  It is **NOT safe** for
        concurrent API thread execution where multiple syncs run in parallel.

        For the API (FastAPI) path, use ``auth.credential_builder.build_*_config()``
        which returns thread-local Pydantic config objects without touching
        ``os.environ``.

    On entry:
        1. Calls ``ensure_valid_token()`` to auto-refresh if needed.
        2. Loads connector-specific credentials from the credential store.
        3. Injects all credentials into ``os.environ``.

    On exit:
        Restores the original environment variables, preventing bleed
        between concurrent pipeline runs.

    Args:
        account: The Account ORM row to resolve credentials for.
        db: Active SQLAlchemy session (for token persistence on refresh).

    Yields:
        The valid access token string.

    Raises:
        TokenExpiredError: If the token cannot be refreshed automatically.
    """
    connector = account.connector_type.upper()
    logger.info(
        "Entering scoped_account_env for %s/%s",
        connector,
        account.identity_email or account.tag,
    )

    # Build the credential map (also refreshes the token if needed)
    cred_map = _load_account_credentials(account, db)

    # Save originals and inject
    saved: Dict[str, Optional[str]] = {}
    for env_var, value in cred_map.items():
        saved[env_var] = os.environ.get(env_var)
        os.environ[env_var] = value

    logger.info(
        "Injected %d env vars for %s/%s",
        len(cred_map),
        connector,
        account.identity_email or account.tag,
    )

    try:
        # Yield the access token for callers that need it directly
        token_key = _CONNECTOR_ENV_MAP.get(connector, {}).get("access_token", "")
        yield cred_map.get(token_key, "")
    finally:
        # Restore originals
        for env_var, original in saved.items():
            if original is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = original

        logger.debug("Restored %d env vars after scoped_account_env", len(saved))
