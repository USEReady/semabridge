"""Dynamic Target Rules Registry: Policies, Capabilities, and SQL Dialects.

Unifies per-target behavior behind one extensible contract so any new target
(Snowflake, Databricks, BigQuery, Fabric Direct Lake, Redshift, ...) registers
its own Policies, Capabilities, and Dialect without editing core routers or
translators.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type


@dataclass
class TargetPolicies:
    """Static platform rules: what is allowed and how objects are named."""

    target_type: str
    display_name: str
    view_kind_label: str
    ai_assistant_name: str
    text_measures_allowed: bool = False
    name_style: str = "uppercase"  # "uppercase" | "keep_original" | "snake_case"
    add_default_calendar_table: bool = False
    declined_measure_strategy: str = "omit_and_comment"  # "null_placeholder" | "omit_and_comment"
    allow_silent_sql_view_fallback: bool = False
    supports_staged_deploy: bool = True
    supports_materialization: bool = False
    supports_multi_fact_split: bool = False
    max_identifier_length: int = 255


@dataclass
class TargetCapabilities:
    """Runtime workspace capabilities discovered by probing the target."""

    probed: bool = False
    measure_references: bool = True  # e.g. MEASURE(`Total Sales`) inside another measure
    window_measures: bool = True  # native YAML window: measures for YTD/MTD/SPLY
    genie_ready: bool = False  # Pro/Serverless SQL warehouse + Databricks Assistant
    cortex_ready: bool = False  # Snowflake Cortex Analyst
    warehouse_state: str = "UNKNOWN"
    probe_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probed": self.probed,
            "measure_references": self.measure_references,
            "window_measures": self.window_measures,
            "genie_ready": self.genie_ready,
            "cortex_ready": self.cortex_ready,
            "warehouse_state": self.warehouse_state,
            "probe_errors": list(self.probe_errors),
        }


class SQLDialect(ABC):
    """Dialect-specific SQL spelling used by measure renderers and query engines."""

    name: str = "generic"

    @abstractmethod
    def quote_identifier(self, identifier: str) -> str:
        """Quote a single SQL identifier."""

    @abstractmethod
    def column(self, table_alias: str, column_name: str) -> str:
        """Render a qualified column reference (e.g. SALES."AMOUNT" vs source.amount)."""

    @abstractmethod
    def measure_reference(self, measure_name: str, inline_sql: Optional[str] = None) -> str:
        """Render a reference to another measure in the same view."""

    @abstractmethod
    def cast_float(self, expr: str) -> str:
        """Cast an expression to a double-precision floating point type."""

    def safe_divide(self, numerator: str, denominator: str) -> str:
        """Render Power BI DIVIDE(a, b) with zero-safe NULLIF."""
        return f"({numerator}) / NULLIF(({denominator}), 0)"

    def concat(self, *parts: str) -> str:
        """Render string concatenation."""
        joined = ", ".join(parts)
        return f"CONCAT({joined})"

    def format_number(self, expr: str, fmt: str) -> str:
        """Render numeric formatting."""
        safe_fmt = fmt.replace("'", "''")
        return f"FORMAT_NUMBER({expr}, '{safe_fmt}')"

    def count_rows(self) -> str:
        """Render COUNTROWS(table)."""
        return "COUNT(1)"


class SnowflakeDialect(SQLDialect):
    """Snowflake SQL spelling for Semantic Views."""

    name = "snowflake"

    def quote_identifier(self, identifier: str) -> str:
        escaped = str(identifier or "").replace('"', '""')
        return f'"{escaped}"'

    def column(self, table_alias: str, column_name: str) -> str:
        col = self.quote_identifier(column_name)
        return f"{table_alias}.{col}" if table_alias else col

    def measure_reference(self, measure_name: str, inline_sql: Optional[str] = None) -> str:
        if inline_sql:
            return f"({inline_sql})"
        return self.quote_identifier(measure_name)

    def cast_float(self, expr: str) -> str:
        return f"({expr})::FLOAT"

    def format_number(self, expr: str, fmt: str) -> str:
        safe_fmt = fmt.replace("'", "''")
        return f"TO_VARCHAR({expr}, '{safe_fmt}')"

    def count_rows(self) -> str:
        return "COUNT(*)"


class DatabricksDialect(SQLDialect):
    """Databricks SQL spelling for Unity Catalog Metric Views."""

    name = "databricks"

    def quote_identifier(self, identifier: str) -> str:
        escaped = str(identifier or "").replace("`", "``")
        return f"`{escaped}`"

    def column(self, table_alias: str, column_name: str) -> str:
        col = str(column_name or "").strip()
        needs_quote = not col.isidentifier()
        col_sql = self.quote_identifier(col) if needs_quote else col
        return f"{table_alias}.{col_sql}" if table_alias else col_sql

    def measure_reference(self, measure_name: str, inline_sql: Optional[str] = None) -> str:
        return f"MEASURE({self.quote_identifier(measure_name)})"

    def cast_float(self, expr: str) -> str:
        return f"CAST({expr} AS DOUBLE)"

    def count_rows(self) -> str:
        return "COUNT(1)"


@dataclass
class TargetRules:
    """Complete platform rulebook: Policies + Capabilities + Dialect."""

    policies: TargetPolicies
    capabilities: TargetCapabilities
    dialect: SQLDialect

    def to_registry_descriptor(self) -> Dict[str, Any]:
        return {
            "id": self.policies.target_type,
            "label": self.policies.display_name,
            "view_kind_label": self.policies.view_kind_label,
            "ai_assistant_name": self.policies.ai_assistant_name,
            "sql_dialect": self.dialect.name,
            "policies": {
                "text_measures_allowed": self.policies.text_measures_allowed,
                "name_style": self.policies.name_style,
                "add_default_calendar_table": self.policies.add_default_calendar_table,
                "declined_measure_strategy": self.policies.declined_measure_strategy,
                "allow_silent_sql_view_fallback": self.policies.allow_silent_sql_view_fallback,
                "supports_staged_deploy": self.policies.supports_staged_deploy,
                "supports_materialization": self.policies.supports_materialization,
                "supports_multi_fact_split": self.policies.supports_multi_fact_split,
                "max_identifier_length": self.policies.max_identifier_length,
            },
            "capabilities": self.capabilities.to_dict(),
        }


# ---------------------------------------------------------------------------
# Dynamic Target Rules Registry
# ---------------------------------------------------------------------------

_TARGET_RULES_FACTORIES: Dict[str, Any] = {}


def register_target_rules(target_type: str):
    """Decorator to register a TargetRules factory for a target connector."""

    key = str(target_type or "").strip().lower()

    def _decorator(fn):
        _TARGET_RULES_FACTORIES[key] = fn
        return fn

    return _decorator


@register_target_rules("snowflake")
def build_snowflake_rules() -> TargetRules:
    return TargetRules(
        policies=TargetPolicies(
            target_type="snowflake",
            display_name="Snowflake",
            view_kind_label="Snowflake Semantic View",
            ai_assistant_name="Snowflake Cortex Analyst",
            text_measures_allowed=False,
            name_style="uppercase",
            add_default_calendar_table=True,
            declined_measure_strategy="null_placeholder",
            allow_silent_sql_view_fallback=False,
            supports_staged_deploy=True,
            supports_materialization=False,
            supports_multi_fact_split=False,
            max_identifier_length=255,
        ),
        capabilities=TargetCapabilities(
            probed=True,
            measure_references=False,
            window_measures=False,
            cortex_ready=True,
        ),
        dialect=SnowflakeDialect(),
    )


@register_target_rules("databricks")
def build_databricks_rules() -> TargetRules:
    return TargetRules(
        policies=TargetPolicies(
            target_type="databricks",
            display_name="Databricks",
            view_kind_label="Databricks Unity Catalog Metric View",
            ai_assistant_name="Databricks AI/BI Genie",
            text_measures_allowed=True,
            name_style="keep_original",
            add_default_calendar_table=False,
            declined_measure_strategy="omit_and_comment",
            allow_silent_sql_view_fallback=False,
            supports_staged_deploy=True,
            supports_materialization=True,
            supports_multi_fact_split=True,
            max_identifier_length=255,
        ),
        capabilities=TargetCapabilities(
            probed=False,
            measure_references=True,
            window_measures=True,
            genie_ready=False,
        ),
        dialect=DatabricksDialect(),
    )


def get_target_rules(target_type: str) -> TargetRules:
    """Return a fresh TargetRules instance for the requested target."""
    key = str(target_type or "snowflake").strip().lower()
    factory = _TARGET_RULES_FACTORIES.get(key)
    if factory is None:
        raise KeyError(f"Unsupported target connector: '{target_type}'. Registered: {sorted(_TARGET_RULES_FACTORIES)}")
    return factory()


def list_registered_targets() -> List[Dict[str, Any]]:
    """Return registry descriptors for all registered targets."""
    return [factory().to_registry_descriptor() for _, factory in sorted(_TARGET_RULES_FACTORIES.items())]


# Dynamic Source Registry (lightweight metadata for GET /api/connectors/registry)
_SOURCE_DESCRIPTORS: Dict[str, Dict[str, Any]] = {
    "pbix": {
        "id": "pbix",
        "label": "Local PBIX File",
        "supports_multi_model": True,
        "supports_live_discovery": False,
        "auth_modes": ["none"],
    },
    "fabric": {
        "id": "fabric",
        "label": "Microsoft Fabric",
        "supports_multi_model": True,
        "supports_live_discovery": True,
        "auth_modes": ["interactive", "service_principal"],
    },
    "tableau": {
        "id": "tableau",
        "label": "Tableau (.tds / .twb)",
        "supports_multi_model": False,
        "supports_live_discovery": False,
        "auth_modes": ["none"],
    },
    "snowflake": {
        "id": "snowflake",
        "label": "Snowflake",
        "supports_multi_model": False,
        "supports_live_discovery": True,
        "auth_modes": ["password", "keypair", "oauth"],
    },
    "databricks": {
        "id": "databricks",
        "label": "Databricks",
        "supports_multi_model": False,
        "supports_live_discovery": True,
        "auth_modes": ["pat", "service_principal", "interactive"],
    },
}


def register_source_descriptor(source_id: str, descriptor: Dict[str, Any]) -> None:
    """Register a source connector descriptor dynamically."""
    key = str(source_id or "").strip().lower()
    _SOURCE_DESCRIPTORS[key] = {"id": key, **descriptor}


def list_registered_sources() -> List[Dict[str, Any]]:
    """Return descriptors for all registered sources."""
    return [dict(v) for _, v in sorted(_SOURCE_DESCRIPTORS.items())]


_DATABRICKS_MESSAGE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Snowflake semantic-view metrics", "Databricks Unity Catalog metric-view measures"),
    ("Snowflake semantic-view metric", "Databricks Unity Catalog metric-view measure"),
    ("Snowflake's semantic-view METRICS clause", "Databricks Unity Catalog metric-view measures"),
    ("Snowflake semantic-view METRICS clause", "Databricks Unity Catalog metric-view measures"),
    ("Snowflake's METRICS clause", "Databricks metric-view measures"),
    ("Snowflake METRICS clause", "Databricks metric-view measures"),
    ("Snowflake's semantic-view compiler", "Databricks Unity Catalog's metric-view compiler"),
    ("Snowflake's semantic-view engine", "Databricks Unity Catalog's metric-view engine"),
    ("Snowflake's semantic-view", "Databricks Unity Catalog's metric-view"),
    ("Snowflake semantic views", "Databricks Unity Catalog metric views"),
    ("Snowflake semantic view", "Databricks Unity Catalog metric view"),
    ("Snowflake semantic-view", "Databricks Unity Catalog metric-view"),
    ("Snowflake Semantic Views", "Databricks Unity Catalog Metric Views"),
    ("Snowflake Semantic View", "Databricks Unity Catalog Metric View"),
    # Security advice (row-level / object-level security not migrated).
    ("implement with a Snowflake row access policy", "implement with a Unity Catalog row filter"),
    ("implement with Snowflake masking policies/grants", "implement with Unity Catalog column masks/grants"),
    ("Snowflake identifiers", "Databricks identifiers"),
    ("Snowflake identifier", "Databricks identifier"),
    ("in Snowflake", "in Databricks"),
    ("by Snowflake", "by Databricks"),
    ("Snowflake cannot compile", "Databricks cannot compile"),
    ("Cortex verified queries", "Genie spaces"),
)


def adapt_target_message(text: Optional[str], target_type: Optional[str] = "snowflake") -> str:
    """Adapt diagnostic/advisory text to the active target connector when target == 'databricks'.

    Returns ``str(text or "")`` unchanged for Snowflake (or any non-Databricks target),
    preserving 100% byte-identical Snowflake behavior.
    """
    raw = str(text or "")
    if not raw or str(target_type or "snowflake").strip().lower() != "databricks":
        return raw
    adapted = raw
    for old, new in _DATABRICKS_MESSAGE_REPLACEMENTS:
        if old in adapted:
            adapted = adapted.replace(old, new)
    return adapted

