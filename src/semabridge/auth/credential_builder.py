"""
Credential Object Builder — Thread-Safe Credential Injection.

Converts an Account credential bundle into connector-specific Pydantic
config objects (SnowflakeConfig, FabricConfig, DatabricksConfig).

Industry pattern (Airbyte / dbt-core / Prefect / Dagster):
  - Airbyte passes config dicts to connector constructors.
  - dbt passes Profile objects to adapter constructors.
  - Prefect passes Block objects to task functions.
  - None of them mutate os.environ for per-run credentials.

This module is the Semabridge equivalent. It:
  1. Decrypts the Account credential bundle.
  2. Performs a layered merge (auth from Account, structural from YAML).
  3. Returns a fully constructed Pydantic config object.

Thread-safety: guaranteed — no global state mutation occurs.
Each call returns a new, independent config object that lives on the
calling thread's stack frame and is garbage-collected when the sync finishes.

Usage::

    from semabridge.auth.credential_builder import build_snowflake_config

    config = build_snowflake_config(account, session, base_config)
    extractor = SnowflakeExtractor(config=config)
    # Fully isolated — no os.environ touched.
"""

from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy.orm import Session

from semabridge.core.settings import DatabricksConfig, FabricConfig, SnowflakeConfig
from semabridge.repository.orm.models import Account
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def _load_credential_env_map(account: Account, db: Session) -> dict[str, str]:
    """Load the env-var-name → value map from an Account, reusing the
    existing ``_load_account_credentials`` logic which handles decrypt,
    legacy fallback, and token refresh.

    Args:
        account: The Account ORM row.
        db: Active SQLAlchemy session (needed for token refresh persistence).

    Returns:
        Dict mapping environment variable names to credential values.
        Example: {"SNOWFLAKE_ACCOUNT": "acme.us-east-1", "SNOWFLAKE_USER": "svc"}
    """
    from semabridge.auth.account_credential_resolver import _load_account_credentials

    return _load_account_credentials(account, db)


_PROJECT_TARGET_FIELDS = (
    ("database", "database"),
    ("schema", "schema_name"),
    ("schema_name", "schema_name"),
    ("source_data_schema", "source_data_schema"),
    ("source-data-schema", "source_data_schema"),
    ("data_schema", "source_data_schema"),
    ("source_schema", "source_data_schema"),
    ("warehouse", "warehouse"),
    ("role", "role"),
)


def project_snowflake_target(config: object) -> dict[str, str]:
    """The structural Snowflake fields the PROJECT configured for its target
    (``targets[0]`` / ``target`` in the project YAML, as loaded by engine
    Step 1 onto ``config.target``), keyed by SnowflakeConfig field name.

    Only non-empty values are returned, and only for a Snowflake (or untyped)
    target. Empty dict when the project configured none of them.
    """
    out: dict[str, str] = {}
    options = getattr(config, "options", None)
    if isinstance(options, dict) and "source_data_schema" in options:
        val = str(options["source_data_schema"] or "").strip()
        if val:
            out["source_data_schema"] = val
    elif hasattr(options, "source_data_schema"):
        val = str(getattr(options, "source_data_schema", "") or "").strip()
        if val:
            out["source_data_schema"] = val

    if hasattr(config, "source_data_schema"):
        val = str(getattr(config, "source_data_schema", "") or "").strip()
        if val:
            out["source_data_schema"] = val

    target = getattr(config, "target", None)
    if target is None:
        return out
    get = target.get if isinstance(target, dict) else (lambda key, default=None: getattr(target, key, default))
    target_type = str(get("type", "") or "").strip().lower()
    if target_type and target_type not in ("snowflake", "snowflake_semantic_view"):
        return out
    for yaml_key, field_name in _PROJECT_TARGET_FIELDS:
        value = str(get(yaml_key, "") or "").strip()
        if value and field_name not in out:
            out[field_name] = value
    return out


def apply_project_snowflake_target(sf_config: SnowflakeConfig, project_target: Optional[dict]) -> SnowflakeConfig:
    """Return a copy of ``sf_config`` with the project's configured structural
    fields (database / schema / warehouse / role) applied. Never mutates the
    input. Identity copy when the project configured none of them."""
    updates = {key: value for key, value in (project_target or {}).items() if value}
    return sf_config.model_copy(update=updates) if updates else sf_config.model_copy()


def build_snowflake_config(
    account: Account,
    db: Session,
    base_config: SnowflakeConfig,
    project_target: Optional[dict] = None,
) -> SnowflakeConfig:
    """Build a thread-safe SnowflakeConfig from the Account credential bundle.

    Layered merge strategy (industry standard — same as dbt Profile merge):
      - Auth fields (user, password, private_key, oauth_*)  → from Account bundle
      - Structural fields (warehouse, database, schema, role) → the PROJECT's
        configured target (``project_target``, see project_snowflake_target)
        when set; otherwise as before (Account bundle, then base_config).
        The schema previously never came from the project at all, so every
        deploy landed in the .env/default schema.

    This guarantees:
    1. Each user's credentials are isolated to their Account row.
    2. Syncs always target the correct project warehouse/database/schema.
    3. No os.environ mutation — fully thread-safe.

    Args:
        account: The Account ORM row containing encrypted credentials.
        db: Active SQLAlchemy session (for token refresh if needed).
        base_config: The global SnowflakeConfig from semabridge.yaml/.env.

    Returns:
        A new SnowflakeConfig instance — immutable, thread-local, garbage-collected
        when the sync finishes.
    """
    env_map = _load_credential_env_map(account, db)
    project_fields = {key: value for key, value in (project_target or {}).items() if value}

    # Resolve base_config password (SecretStr → str) for fallback
    base_password: Optional[str] = None
    if base_config.password:
        base_password = base_config.password.get_secret_value()

    # Resolve base_config oauth_client_secret (SecretStr → str)
    base_oauth_secret: Optional[str] = None
    if base_config.oauth_client_secret:
        base_oauth_secret = base_config.oauth_client_secret.get_secret_value()

    # Resolve base_config private_key_passphrase (SecretStr → str)
    base_passphrase: Optional[str] = None
    if base_config.private_key_passphrase:
        base_passphrase = base_config.private_key_passphrase.get_secret_value()

    import os as _os
    sf_account = (
        env_map.get("SNOWFLAKE_ACCOUNT")
        or base_config.account
        # Last-resort: check os.environ directly. This handles legacy accounts
        # whose encrypted_token predates the JSON-bundle format (so env_map is
        # empty) when saveConnection previously injected the value into the
        # process environment.
        or _os.environ.get("SNOWFLAKE_ACCOUNT", "")
    )
    if not sf_account or sf_account == "placeholder":
        raise ValueError(
            f"Snowflake account identifier is missing for account '{account.tag}'. "
            "Edit the connection in Settings → Connections and enter the Account URL "
            "(e.g. abc123.us-east-1)."
        )
    # Normalize: strip full URLs down to just the account identifier
    sf_account = sf_account.strip()
    for _pfx in ("https://", "http://"):
        if sf_account.lower().startswith(_pfx):
            sf_account = sf_account[len(_pfx):]
    if ".snowflakecomputing.com" in sf_account.lower():
        sf_account = sf_account.lower().split(".snowflakecomputing.com")[0]

    config = SnowflakeConfig(
        # Auth — always from Account bundle (user-specific)
        account=sf_account,
        user=env_map.get("SNOWFLAKE_USER") or base_config.user,
        password=env_map.get("SNOWFLAKE_PASSWORD") or base_password,
        auth_type=env_map.get("SNOWFLAKE_AUTH_TYPE") or base_config.auth_type,
        private_key=env_map.get("SNOWFLAKE_PRIVATE_KEY") or base_config.private_key,
        private_key_passphrase=env_map.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or base_passphrase,
        oauth_client_id=env_map.get("SNOWFLAKE_OAUTH_CLIENT_ID") or base_config.oauth_client_id,
        oauth_client_secret=env_map.get("SNOWFLAKE_OAUTH_CLIENT_SECRET") or base_oauth_secret,
        oauth_token_endpoint=env_map.get("SNOWFLAKE_OAUTH_TOKEN_ENDPOINT") or base_config.oauth_token_endpoint,
        oauth_scope=env_map.get("SNOWFLAKE_OAUTH_SCOPE") or base_config.oauth_scope,
        # Structural — the project's configured target wins; otherwise as before.
        warehouse=project_fields.get("warehouse") or env_map.get("SNOWFLAKE_WAREHOUSE") or base_config.warehouse,
        database=project_fields.get("database") or env_map.get("SNOWFLAKE_DATABASE") or base_config.database,
        schema_name=project_fields.get("schema_name") or base_config.schema_name,
        source_data_schema=project_fields.get("source_data_schema") or base_config.source_data_schema,
        strict_osi_validation=env_map.get("SNOWFLAKE_STRICT_OSI_VALIDATION") == "true" or base_config.strict_osi_validation,
        role=project_fields.get("role") or env_map.get("SNOWFLAKE_ROLE") or base_config.role,
        # Passthrough — always from base config
        deployment_method=base_config.deployment_method,
        cortex_search_service=base_config.cortex_search_service,
        push_run_summary_to_snowflake=base_config.push_run_summary_to_snowflake,
    )

    logger.info(
        "Built scoped SnowflakeConfig for account %s/%s (user=%s, db=%s) — "
        "no os.environ mutation.",
        account.connector_type,
        account.tag,
        config.user,
        config.database,
    )
    return config


def build_fabric_config(
    account: Account,
    db: Session,
    base_config: FabricConfig,
) -> Tuple[FabricConfig, Optional[str]]:
    """Build a thread-safe FabricConfig + access token from Account bundle.

    The access_token is returned separately because FabricExtractor.__init__()
    already accepts it as a constructor argument (``access_token: Optional[str]``).

    Args:
        account: Account row.
        db: Active session.
        base_config: Global FabricConfig from semabridge.yaml/.env.

    Returns:
        Tuple of (FabricConfig, access_token_str or None).
    """
    env_map = _load_credential_env_map(account, db)

    # Resolve base_config client_secret (SecretStr → str)
    base_client_secret: Optional[str] = None
    if base_config.client_secret:
        base_client_secret = base_config.client_secret.get_secret_value()

    fabric_config = FabricConfig(
        tenant_id=env_map.get("FABRIC_TENANT_ID") or base_config.tenant_id,
        client_id=env_map.get("FABRIC_CLIENT_ID") or base_config.client_id,
        client_secret=env_map.get("FABRIC_CLIENT_SECRET") or base_client_secret,
        workspace_id=env_map.get("FABRIC_WORKSPACE_ID") or base_config.workspace_id,
        # Passthrough — always from base config
        api_base_url=base_config.api_base_url,
        power_bi_api_url=base_config.power_bi_api_url,
    )

    access_token = env_map.get("FABRIC_ACCESS_TOKEN")

    logger.info(
        "Built scoped FabricConfig for account %s/%s (tenant=%s, workspace=%s, "
        "has_token=%s) — no os.environ mutation.",
        account.connector_type,
        account.tag,
        (fabric_config.tenant_id or "")[:8] + "...",
        (fabric_config.workspace_id or "")[:8] + "...",
        bool(access_token),
    )
    return fabric_config, access_token


def build_databricks_config(
    account: Account,
    db: Session,
    base_config: DatabricksConfig,
) -> DatabricksConfig:
    """Build a thread-safe DatabricksConfig from Account bundle.

    Args:
        account: Account row.
        db: Active session.
        base_config: Global DatabricksConfig from semabridge.yaml/.env.

    Returns:
        A new DatabricksConfig instance.
    """
    env_map = _load_credential_env_map(account, db)

    # Resolve base_config token (SecretStr → str)
    base_token: Optional[str] = None
    if base_config.token:
        base_token = base_config.token.get_secret_value()

    # Resolve base_config client_secret (SecretStr → str)
    base_client_secret: Optional[str] = None
    if base_config.client_secret:
        base_client_secret = base_config.client_secret.get_secret_value()

    config = DatabricksConfig(
        host=env_map.get("DATABRICKS_HOST") or base_config.host,
        token=env_map.get("DATABRICKS_TOKEN") or base_token,
        auth_type=env_map.get("DATABRICKS_AUTH_TYPE") or base_config.auth_type,
        client_id=env_map.get("DATABRICKS_CLIENT_ID") or base_config.client_id,
        client_secret=env_map.get("DATABRICKS_CLIENT_SECRET") or base_client_secret,
        warehouse_id=env_map.get("DATABRICKS_WAREHOUSE_ID") or base_config.warehouse_id,
        # Structural — from base config
        catalog=base_config.catalog,
        schema_name=base_config.schema_name,
    )

    logger.info(
        "Built scoped DatabricksConfig for account %s/%s (host=%s) — "
        "no os.environ mutation.",
        account.connector_type,
        account.tag,
        config.host,
    )
    return config
