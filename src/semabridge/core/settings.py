"""
Configuration settings for Semabridge.

Uses Pydantic Settings for type-safe configuration loading from environment
variables and .env files. Avoids the issues from semantic-sync by:
1. Clear separation of config concerns (Snowflake, Fabric, Model)
2. No nested Settings objects that cause attribute errors
3. Explicit validation with helpful error messages
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from semabridge.core.config_loader import get_project_file_path

# Dialects that use an in-process connection (no external server, no SSL)
_IN_PROCESS_DIALECTS: frozenset[str] = frozenset({"sqlite", "duckdb"})


class SnowflakeConfig(BaseSettings):
    """Snowflake connection configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="SNOWFLAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    account: str = Field(..., description="Snowflake account identifier (e.g., abc123.us-east-1)")
    user: str = Field(..., description="Snowflake username")
    password: Optional[SecretStr] = Field(default=None, description="Snowflake password (required for password auth)")
    auth_type: str = Field(default="password", description="Auth method: password | keypair | externalbrowser")
    private_key: Optional[str] = Field(default=None, description="PEM-encoded private key for Key Pair auth")
    private_key_passphrase: Optional[SecretStr] = Field(default=None, description="Passphrase for encrypted private key")
    authenticator: Optional[str] = Field(default=None, description="Snowflake authenticator (externalbrowser for SSO)")
    warehouse: str = Field(..., description="Snowflake warehouse name")
    database: str = Field(..., description="Snowflake database name")
    schema_name: str = Field(default="PUBLIC", validation_alias="SNOWFLAKE_SCHEMA", description="Snowflake schema name")
    role: Optional[str] = Field(default=None, description="Snowflake role (optional)")

    # Cortex Analyst integration
    cortex_search_service: Optional[str] = Field(
        default=None,
        description="Snowflake Cortex Search Service name to link to text dimensions for RAG"
    )
    deployment_method: str = Field(
        default="ddl",
        description=(
            "Semantic view deployment strategy: "
            "'ddl' (direct CREATE OR REPLACE SEMANTIC VIEW), "
            "'yaml_stored_procedure' (CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML), "
            "or 'both'"
        ),
    )
    push_run_summary_to_snowflake: bool = Field(
        default=False,
        description="If True, insert RunSummary JSON into SEMABRIDGE_RUNS observability table after each run",
    )
    
    @field_validator("account")
    @classmethod
    def validate_account(cls, v: str) -> str:
        """Ensure account identifier is properly formatted."""
        if not v or v == "your-account.region":
            raise ValueError("SNOWFLAKE_ACCOUNT must be set to your actual Snowflake account")
        return v.strip()


class FabricConfig(BaseSettings):
    """Microsoft Fabric configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="FABRIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    tenant_id: str = Field(..., description="Azure AD tenant ID")
    client_id: str = Field(..., description="Azure AD application (client) ID")
    # client_secret is only required for service-principal (client-credentials) flow.
    # Leave unset when using interactive device-code flow.
    client_secret: Optional[SecretStr] = Field(
        default=None,
        description="Azure AD client secret (not required for device-code / delegated flow)",
    )
    workspace_id: str = Field(..., description="Primary Fabric workspace ID")
    
    # Multi-workspace support
    workspace_ids: List[str] = Field(
        default_factory=list,
        description="Additional Fabric workspace IDs (comma-separated in env)"
    )
    default_workspace_id: Optional[str] = Field(
        default=None,
        description="Default workspace ID to use when none is specified"
    )
    
    # API endpoints
    api_base_url: str = Field(
        default="https://api.fabric.microsoft.com/v1",
        description="Fabric REST API base URL"
    )
    power_bi_api_url: str = Field(
        default="https://api.powerbi.com/v1.0/myorg",
        description="Power BI REST API base URL"
    )

    @property
    def all_workspace_ids(self) -> List[str]:
        """Get all known workspace IDs (primary + configured list)."""
        ids = [self.workspace_id]
        for wid in self.workspace_ids:
            if wid and wid not in ids:
                ids.append(wid)
        return ids

    @property
    def resolved_default_workspace_id(self) -> str:
        """Return the default workspace ID, falling back to the primary."""
        return self.default_workspace_id or self.workspace_id
    
    @field_validator("tenant_id", "client_id", "workspace_id")
    @classmethod
    def validate_guid(cls, v: str, info) -> str:
        """Validate GUID format. 'organizations' is accepted as a valid tenant."""
        if not v or v.startswith("your-"):
            raise ValueError(f"FABRIC_{info.field_name.upper()} must be set to a valid value")
        return v.strip()


class DatabricksConfig(BaseSettings):
    """Databricks SQL Warehouse configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DATABRICKS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(..., description="Databricks workspace host, e.g. adb-12345.11.azuredatabricks.net")
    token: SecretStr = Field(..., description="Databricks personal access token")
    warehouse_id: str = Field(..., description="Databricks SQL warehouse ID for statement execution")
    catalog: str = Field(default="main", description="Unity Catalog catalog name for semantic objects")
    schema_name: str = Field(default="semabridge", validation_alias="DATABRICKS_SCHEMA", description="Unity Catalog schema for semantic objects")

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        if not v or v.startswith("your-"):
            raise ValueError("DATABRICKS_HOST must be set to your Databricks workspace host")
        val = v.strip().rstrip("/")
        if val.startswith("https://"):
            val = val[len("https://"):]
        elif val.startswith("http://"):
            val = val[len("http://"):]
        return val

    @property
    def api_base_url(self) -> str:
        return f"https://{self.host}"


class ModelConfig(BaseSettings):
    """Semantic model configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="MODEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    name: str = Field(
        default="SnowflakeSemanticModel",
        description="Name of the semantic model to create"
    )
    description: str = Field(
        default="Auto-generated semantic model from Snowflake metadata",
        description="Description of the semantic model"
    )
    exclude_tables: str = Field(
        default="_SEMANTIC_METADATA,_SEMANTIC_MEASURES,_SEMANTIC_RELATIONSHIPS,_SEMANTIC_SYNC_HISTORY,_SEMANTIC_COLUMNS",
        description="Comma-separated list of tables to exclude"
    )
    include_tables: Optional[str] = Field(
        default=None,
        description="Comma-separated list of tables to include (if set, only these tables are processed)"
    )
    
    # Performance settings
    cache_enabled: bool = Field(
        default=True,
        description="Enable incremental processing cache"
    )
    cache_dir: str = Field(
        default=".semabridge_cache",
        description="Directory for cache files"
    )
    
    @property
    def included_table_list(self) -> list[str] | None:
        """Get list of included tables (None means all)."""
        if not self.include_tables:
            return None
        return [t.strip().upper() for t in self.include_tables.split(",") if t.strip()]

    @property
    def excluded_table_list(self) -> list[str]:
        """Get list of excluded tables."""
        if not self.exclude_tables:
            return []
        return [t.strip().upper() for t in self.exclude_tables.split(",") if t.strip()]


class LLMConfig(BaseSettings):
    """Configuration for LLM-based features (Cortex, OpenAI)."""

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: str = Field(
        default="snowflake",
        description="LLM provider: 'snowflake' (Cortex) or 'openai'"
    )
    model: str = Field(
        default="snowflake-arctic",
        description="Model name to use"
    )
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="Optional API key for provider"
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(
        env_prefix="LOGGING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    level: str = Field(default="INFO", description="Logging level")
    format: Optional[str] = Field(
        default="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        description="Log format string"
    )
    log_file: Optional[str] = Field(
        default=None,
        description="Explicit log file path. Defaults to co-located with DuckDB repo."
    )

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        """Validate logging level."""
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed:
            raise ValueError(f"Invalid log level '{v}'. Allowed values: {', '.join(allowed)}")
        return v.upper()


class ConcurrencyConfig(BaseSettings):
    """Concurrency and parallel processing configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    max_workers: int = Field(
        default=5,
        validation_alias="MAX_WORKERS",
        description="Maximum number of concurrent worker threads",
    )
    enable_parallel: bool = Field(
        default=False,
        validation_alias="PARALLEL_ENABLED",
        description="Enable parallel execution mode globally",
    )

    @field_validator("max_workers")
    @classmethod
    def validate_max_workers(cls, v: int) -> int:
        """Ensure max_workers is within safe bounds."""
        if v < 1:
            raise ValueError("MAX_WORKERS must be >= 1")
        if v > 32:
            raise ValueError("MAX_WORKERS must be <= 32 to avoid connection exhaustion")
        return v


class TelemetryConfig(BaseSettings):
    """OpenTelemetry + Snowflake observability configuration."""

    model_config = SettingsConfigDict(
        env_prefix="TELEMETRY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry tracing and metrics export",
    )
    otlp_endpoint: Optional[str] = Field(
        default=None,
        description="OTLP gRPC collector endpoint (e.g. http://localhost:4317). "
                    "When None, spans are emitted to the console only.",
    )
    service_name: str = Field(
        default="semabridge",
        description="OTel service.name resource attribute",
    )
    insecure: bool = Field(
        default=True,
        description="Skip TLS verification for OTLP gRPC channel (dev / on-prem)",
    )


class DatabaseConfig(BaseSettings):
    """Database connection and pool configuration.

    A single ``SEMABRIDGE_DATABASE_URL`` env-var drives the entire database
    layer.  The dialect is auto-detected from the URL scheme:

    * ``sqlite:///path/to/db.sqlite`` — SQLite file or ``sqlite:///:memory:``
    * ``duckdb:///path/to/db.duckdb`` — embedded DuckDB file
    * ``postgresql+psycopg2://user:pass@host:5432/semabridge`` — PostgreSQL
    * ``mysql+pymysql://user:pass@host:3306/semabridge`` — MySQL

    Pool settings (``DB_POOL_SIZE`` etc.) are ignored for in-process dialects
    (SQLite, DuckDB) because those use ``StaticPool``.

    SSL settings apply to server-based dialects only.  Pass the ``sslmode``
    understood by your driver:
    * psycopg2: ``disable | allow | prefer | require | verify-ca | verify-full``
    * PyMySQL:  ``DISABLED | PREFERRED | REQUIRED | VERIFY_CA | VERIFY_IDENTITY``
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    url: str = Field(
        default="",
        validation_alias="SEMABRIDGE_DATABASE_URL",
        description=(
            "SQLAlchemy connection URL.  Required for all backends. "
            "Falls back to a DuckDB file at ~/.semabridge/semabridge_state.db when unset."
        ),
    )

    # ---- Connection pool (server dialects only) -------------------------
    pool_size: int = Field(
        default=5,
        validation_alias="DB_POOL_SIZE",
        description="Number of persistent connections in the pool (PostgreSQL / MySQL).",
    )
    max_overflow: int = Field(
        default=10,
        validation_alias="DB_MAX_OVERFLOW",
        description="Extra connections allowed beyond pool_size under load.",
    )
    pool_recycle: int = Field(
        default=1800,
        validation_alias="DB_POOL_RECYCLE",
        description="Seconds before a connection is forcibly recycled (avoids stale connections).",
    )
    pool_pre_ping: bool = Field(
        default=True,
        validation_alias="DB_POOL_PRE_PING",
        description="Validate connections with a cheap SELECT before checkout.",
    )

    # ---- SSL (server dialects only) ------------------------------------
    ssl_mode: Optional[str] = Field(
        default=None,
        validation_alias="DB_SSL_MODE",
        description=(
            "SSL mode for server databases. "
            "PostgreSQL: disable|require|verify-ca|verify-full. "
            "MySQL: DISABLED|REQUIRED|VERIFY_CA|VERIFY_IDENTITY."
        ),
    )
    ssl_ca: Optional[str] = Field(
        default=None,
        validation_alias="DB_SSL_CA",
        description="Path to the CA certificate file for SSL verification.",
    )

    # ---- Schema namespace (PostgreSQL / MySQL) -------------------------
    schema_name: Optional[str] = Field(
        default=None,
        validation_alias="DB_SCHEMA",
        description=(
            "Target schema / database name used when creating tables. "
            "Defaults to the schema embedded in the URL."
        ),
    )

    # ---- Debug ---------------------------------------------------------
    echo: bool = Field(
        default=False,
        validation_alias="DB_ECHO",
        description="Set True to log all SQL statements emitted by SQLAlchemy.",
    )

    # ---- Derived helpers -----------------------------------------------

    @property
    def dialect(self) -> str:
        """Return the lowercase dialect name derived from the URL scheme."""
        if not self.url or "://" not in self.url:
            return "duckdb"
        return self.url.split("://")[0].split("+")[0].lower()

    @property
    def is_in_process(self) -> bool:
        """True for SQLite and DuckDB (no external server, use StaticPool)."""
        return self.dialect in _IN_PROCESS_DIALECTS

    @property
    def resolved_url(self) -> str:
        """Return ``url`` or the default DuckDB path when url is empty."""
        if self.url:
            return self.url
        from pathlib import Path as _Path
        sema_dir = _Path.home() / ".semabridge"
        sema_dir.mkdir(parents=True, exist_ok=True)
        return f"duckdb:///{sema_dir / 'semabridge_state.db'}"

    @field_validator("pool_size")
    @classmethod
    def validate_pool_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError("DB_POOL_SIZE must be >= 1")
        if v > 50:
            raise ValueError("DB_POOL_SIZE must be <= 50 to avoid connection exhaustion")
        return v

    @field_validator("max_overflow")
    @classmethod
    def validate_max_overflow(cls, v: int) -> int:
        if v < 0:
            raise ValueError("DB_MAX_OVERFLOW must be >= 0")
        return v


class Settings(BaseSettings):
    """
    Combined settings for the entire application.
    
    Unlike semantic-sync, we use composition with explicit loading
    to avoid attribute access issues.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Sub-configurations are loaded separately to avoid nesting issues
    _snowflake: Optional[SnowflakeConfig] = None
    _fabric: Optional[FabricConfig] = None
    _databricks: Optional[DatabricksConfig] = None
    _model: Optional[ModelConfig] = None
    _logging: Optional[LoggingConfig] = None
    _concurrency: Optional[ConcurrencyConfig] = None
    _telemetry: Optional[TelemetryConfig] = None
    _database: Optional[DatabaseConfig] = None
    _llm: Optional[LLMConfig] = None
    _behavior: Optional[object] = None  # ConnectorBehavior (lazy, avoids circular import)

    @property
    def snowflake(self) -> SnowflakeConfig:
        """Get Snowflake configuration (lazy loaded)."""
        if self._snowflake is None:
            self._snowflake = SnowflakeConfig()
        return self._snowflake
    
    @property
    def fabric(self) -> FabricConfig:
        """Get Fabric configuration (lazy loaded)."""
        if self._fabric is None:
            self._fabric = FabricConfig()
        return self._fabric
    
    @property
    def model(self) -> ModelConfig:
        """Get model configuration (lazy loaded)."""
        if self._model is None:
            self._model = ModelConfig()
        return self._model

    @property
    def databricks(self) -> DatabricksConfig:
        """Get Databricks configuration (lazy loaded)."""
        if self._databricks is None:
            self._databricks = DatabricksConfig()
        return self._databricks
    
    @property
    def logging(self) -> LoggingConfig:
        """Get logging configuration (lazy loaded)."""
        if self._logging is None:
            self._logging = LoggingConfig()
        return self._logging
    
    @property
    def concurrency(self) -> ConcurrencyConfig:
        """Get concurrency configuration (lazy loaded)."""
        if self._concurrency is None:
            self._concurrency = ConcurrencyConfig()
        return self._concurrency

    @property
    def telemetry(self) -> TelemetryConfig:
        """Get telemetry configuration (lazy loaded)."""
        if self._telemetry is None:
            self._telemetry = TelemetryConfig()
        return self._telemetry

    @property
    def database(self) -> DatabaseConfig:
        """Get database configuration (lazy loaded).

        The resolved URL is available via ``settings.database.resolved_url``.
        Dialect-specific helpers: ``settings.database.dialect``,
        ``settings.database.is_in_process``.
        """
        if self._database is None:
            self._database = DatabaseConfig()
        return self._database

    @property
    def llm(self) -> LLMConfig:
        """Get LLM configuration (lazy loaded)."""
        if self._llm is None:
            self._llm = LLMConfig()
        return self._llm

    @property
    def behavior(self):
        """Get behavior policy (lazy-loaded from behavior.yaml if present).

        Resolution order:
        1. Already-cached instance (avoids repeated YAML parsing).
        2. ``behavior.yaml`` in the current working directory.
        3. Default ``ConnectorBehavior()`` with all defaults.

        This property is intentionally typed as ``object`` in the private
        field to avoid a circular import — callers should treat the return
        value as ``ConnectorBehavior``.
        """
        if self._behavior is None:
            from semabridge.core.behavior import ConnectorBehavior
            behavior_path = get_project_file_path("behavior.yaml")
            if behavior_path.exists():
                try:
                    self._behavior = ConnectorBehavior.from_yaml(behavior_path)
                except Exception:
                    # Malformed behavior.yaml — fall back to defaults rather than crash
                    self._behavior = ConnectorBehavior()
            else:
                self._behavior = ConnectorBehavior()
        return self._behavior

    def validate_snowflake(self) -> bool:
        """Validate Snowflake configuration is complete."""
        try:
            _ = self.snowflake
            return True
        except Exception:
            return False
    
    def validate_fabric(self) -> bool:
        """Validate Fabric configuration is complete."""
        try:
            _ = self.fabric
            return True
        except Exception:
            return False

    def validate_databricks(self) -> bool:
        """Validate Databricks configuration is complete."""
        try:
            _ = self.databricks
            return True
        except Exception:
            return False


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached application settings.
    
    Uses lru_cache to ensure settings are loaded only once.
    To reload settings, call get_settings.cache_clear().
    """
    # Ensure we're looking for .env in the right place
    env_file = Path.cwd() / ".env"
    if not env_file.exists():
        # Try parent directories
        for parent in Path.cwd().parents:
            candidate = parent / ".env"
            if candidate.exists():
                os.chdir(parent)
                break
    
    return Settings()


def reload_settings() -> Settings:
    """Force reload of settings (clears cache)."""
    get_settings.cache_clear()
    return get_settings()


def resolve_workspace_id(
    cli_flag: Optional[str] = None,
    prompt_fallback: bool = True,
) -> str:
    """
    Resolve the active Fabric workspace ID using a strict precedence chain.

    Resolution order:
      1. CLI flag (--workspace-id)
      2. Project semabridge.yaml -> fabric.default_workspace_id
      3. Global config.yaml     -> fabric.default_workspace_id
      4. FABRIC_WORKSPACE_ID env var (loaded via FabricConfig)
      5. Interactive prompt (if prompt_fallback is True)

    Args:
        cli_flag: Workspace ID supplied via the CLI.
        prompt_fallback: If True and nothing found, prompt the user.

    Returns:
        Resolved workspace ID string.

    Raises:
        ValueError: If no workspace ID can be determined.
    """
    if cli_flag:
        return cli_flag

    # 2. Project-level semabridge.yaml
    try:
        import yaml as _yaml
        p = get_project_file_path("semabridge.yaml")
        if p.exists():
            cfg = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            val = cfg.get("fabric", {}).get("default_workspace_id")
            if val:
                return val
    except Exception:
        pass

    # 3. Global config.yaml
    try:
        import yaml as _yaml
        g = Path.home() / ".semabridge" / "config.yaml"
        if g.exists():
            cfg = _yaml.safe_load(g.read_text(encoding="utf-8")) or {}
            val = cfg.get("fabric", {}).get("default_workspace_id")
            if val:
                return val
    except Exception:
        pass

    # 4. Env-var via FabricConfig
    try:
        s = get_settings()
        return s.fabric.resolved_default_workspace_id
    except Exception:
        pass

    # 5. Interactive prompt (only works in CLI context)
    if prompt_fallback:
        try:
            import typer
            return typer.prompt("No workspace ID configured. Enter Fabric Workspace ID")
        except Exception:
            pass

    raise ValueError(
        "No workspace ID configured. Set FABRIC_WORKSPACE_ID in .env, "
        "or use --workspace-id, or run 'semabridge init'."
    )
