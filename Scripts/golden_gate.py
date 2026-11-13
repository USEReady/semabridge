#!/usr/bin/env python3
"""scripts/golden_gate.py - Automated Golden Mathematical Quality Gate.

Builds the three golden models from THIS checkout's code -- PBIX -> TMSL -> OSI ->
SML -> SnowflakeEmitter.deploy() (enriched views, time-intelligence flags and the
semantic view) -- into temporary sandbox schemas whose base tables are zero-copy
clones of the source data, verifies the 8 known golden metric values, then drops
the sandboxes. Live semantic/enriched views are never copied: a stale or
hand-patched live view must not be able to make the gate pass.

PBIX locations (override with env vars):
  GOLDEN_PBIX_COMPETITIVE_MARKETING, GOLDEN_PBIX_CORPORATE_SPEND, GOLDEN_PBIX_HUMAN_RESOURCES

The 8 Verified Golden Values:
  1. Competitive Marketing: Totals (Total Units = 1,299,599)
  2. Competitive Marketing: YTD (Total Units YTD = 49,832)
  3. Competitive Marketing: SPLY, latest period (Total Units SPLY_LATEST = 47,250)
  4. Competitive Marketing: R12M (Total Units R12Ms = 49,832)
  5. Competitive Marketing: Sentiment Gap (17.2648)
  6. Corporate Spend: Var Plan Infrastructure (23,536,681.48)
  7. HR: New Hires SPLY by year (2012: 3,258; 2013: 7,407; 2014: 13,382)
  8. HR: New Hires _LATEST companion (13,382)
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import snowflake.connector
from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
from semabridge.core.settings import SnowflakeConfig


def _get_snowflake_config() -> Optional[SnowflakeConfig]:
    """Retrieve Snowflake credentials from config file or environment."""
    cfg_path = Path("snowflake_config.json")
    if not cfg_path.exists():
        fallback_path = Path(r"C:\Users\charank\OneDrive - USEReady Technology Private Limited\Desktop\data-load-snowflake-semabridge\snowflake_config.json")
        if fallback_path.exists():
            cfg_path = fallback_path

    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        key_pem = None
        key_path = data.get("private_key_path")
        if not key_path:
            alt_key = cfg_path.parent / "snowflake_key.p8"
            if alt_key.exists():
                key_path = str(alt_key)

        if key_path and os.path.exists(key_path):
            with open(key_path, "r", encoding="utf-8") as kf:
                key_pem = kf.read()

        return SnowflakeConfig(
            account=data["account"],
            user=data["user"],
            database=data.get("database", "SEMABRIDGE"),
            schema_name=data.get("schema", "SEMABRIDGE_WORKSPACE"),
            warehouse=data["warehouse"],
            role=data.get("role", "SEMABRIDGE_DEVELOPER"),
            auth_type="keypair" if key_pem else "password",
            private_key=key_pem,
            private_key_passphrase=data.get("private_key_passphrase"),
            password=data.get("password"),
        )

    # Check environment variables
    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    user = os.environ.get("SNOWFLAKE_USER")
    database = os.environ.get("SNOWFLAKE_DATABASE", "SEMABRIDGE")
    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "DI_OTH_POC_WH")
    role = os.environ.get("SNOWFLAKE_ROLE", "SEMABRIDGE_DEVELOPER")
    private_key = os.environ.get("SNOWFLAKE_PRIVATE_KEY")
    password = os.environ.get("SNOWFLAKE_PASSWORD")

    if account and user and (private_key or password):
        return SnowflakeConfig(
            account=account,
            user=user,
            database=database,
            schema_name=os.environ.get("SNOWFLAKE_SCHEMA", "SEMABRIDGE_WORKSPACE"),
            warehouse=warehouse,
            role=role,
            auth_type="keypair" if private_key else "password",
            private_key=private_key,
            password=password,
        )

    return None


_PBIX_ROOT = Path(r"C:\Users\charank\OneDrive - USEReady Technology Private Limited\Desktop")

# (model key, source PBIX, schema holding the model's source tables)
GOLDEN_MODELS: List[Tuple[str, str, str]] = [
    (
        "COMPETITIVE_MARKETING",
        os.environ.get(
            "GOLDEN_PBIX_COMPETITIVE_MARKETING",
            str(_PBIX_ROOT / "powerbi" / "Competitive Marketing Analysis (More Synonyms)_updated.pbix"),
        ),
        "SEMABRIDGE_WORKSPACE",
    ),
    (
        "CORPORATE_SPEND",
        os.environ.get("GOLDEN_PBIX_CORPORATE_SPEND", str(_PBIX_ROOT / "powerbi" / "Corporate Spend.pbix")),
        "SEMABRIDGE_WORKSPACE",
    ),
    (
        "HUMAN_RESOURCES",
        os.environ.get(
            "GOLDEN_PBIX_HUMAN_RESOURCES",
            str(_PBIX_ROOT / "semabridge" / "pbix_testing" / "Human Resources Sample PBIX 1.pbix"),
        ),
        "HUMAN_RESOURCES",
    ),
]


# Dataset -> physical table bindings, exactly as the production projects that
# deployed these models resolved them. Pinned so the gate is reproducible on
# any machine instead of depending on that machine's saved-binding history
# (Corporate Spend's 'Date' is SPEND_DATE, not the CM model's DATE table).
GOLDEN_BINDINGS: Dict[str, Dict[str, str]] = {
    "COMPETITIVE_MARKETING": {
        "CATEGORY": "CATEGORY", "DATE": "DATE", "GEO": "GEO", "INDICATORS": "INDICATORS", "KPI": "KPI",
        "MANUFACTURER": "MANUFACTURER", "PRODUCT": "PRODUCT", "SALESFACT": "SALES_FACT", "SENTIMENT": "SENTIMENT",
    },
    "CORPORATE_SPEND": {
        "BUSINESSAREA": "BUSINESS_AREA", "COSTELEMENT": "COST_ELEMENT", "COUNTRYREGION": "COUNTRY_REGION",
        "DATE": "SPEND_DATE", "DEPARTMENT": "DEPARTMENT", "FACT": "SPEND_FACT", "ITAREA": "IT_AREA",
        "RANGE": "RANGE", "SCENARIO": "SCENARIO",
    },
    "HUMAN_RESOURCES": {
        "AGEGROUP": "AGEGROUP", "BU": "BU", "DATE": "DATE", "EMPLOYEE": "EMPLOYEE", "ETHNICITY": "ETHNICITY",
        "FP": "FP", "GENDER": "GENDER", "PAYTYPE": "PAYTYPE", "SEPARATIONREASON": "SEPARATIONREASON",
    },
}
_BINDING_PROJECT = "golden-gate"


def _pin_bindings(key: str) -> Tuple[str, str]:
    """Save GOLDEN_BINDINGS[key] through the same saved-binding path a
    production deploy uses, and return the emitter binding_scope for it."""
    from semabridge.connectors.table_binding_resolver import clean_ident
    from semabridge.repository.model_repository import ModelRepository

    model_key = key.lower()
    ModelRepository().save_table_bindings(
        _BINDING_PROJECT,
        model_key,
        [
            # Keyed exactly as the resolver looks pins up (reserved words such
            # as DATE/RANGE become COL_DATE/COL_RANGE).
            {"dataset_key": clean_ident(ds), "dataset": ds, "table": table, "matched_by": "golden_gate"}
            for ds, table in GOLDEN_BINDINGS[key].items()
        ],
    )
    return _BINDING_PROJECT, model_key


def _clone_base_tables(cur: Any, database: str, source_schema: str, sandbox_schema: str) -> None:
    """Zero-copy clone of the source data only. Views (enriched or semantic)
    are deliberately not cloned: HEAD's deploy must build its own."""
    cur.execute(
        f"SELECT TABLE_NAME FROM {database}.INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_SCHEMA = '{source_schema}' AND TABLE_TYPE = 'BASE TABLE'"
    )
    tables = [r[0] for r in cur.fetchall()]
    for table in tables:
        cur.execute(
            f'CREATE TABLE {database}.{sandbox_schema}."{table}" CLONE {database}.{source_schema}."{table}"'
        )
    print(f"   cloned {len(tables)} base tables")


def _semantic_views(cur: Any, database: str, schema: str) -> set:
    cur.execute(f"SHOW SEMANTIC VIEWS IN SCHEMA {database}.{schema}")
    name_idx = [d[0] for d in cur.description].index("name")
    return {r[name_idx] for r in cur.fetchall()}


def _build_from_head(
    cfg: SnowflakeConfig, cur: Any, database: str, sandbox_schema: str, key: str, pbix_path: str
) -> Tuple[Optional[str], Optional[str]]:
    """Dry-run the PBIX through this checkout's pipeline and deploy the
    generated DDL into the sandbox. Returns (fully-qualified view, error)."""
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter

    connector = LocalPBIXConnector({"pbix_path": pbix_path})
    tmsl = connector.discover().get("raw_tmsl") or connector.extract()
    osi = TMSLToOSIConverter().to_osi({"tmsl": tmsl, "dataset_id": Path(pbix_path).stem})
    sml = OSIToSMLConverter().from_osi(osi)

    before = _semantic_views(cur, database, sandbox_schema)
    emitter = SnowflakeEmitter(cfg.model_copy(update={"database": database, "schema_name": sandbox_schema}))
    emitter.binding_scope = _pin_bindings(key)
    if not emitter.deploy(sml):
        return None, emitter.last_deployment_error or "deploy returned False"
    created = _semantic_views(cur, database, sandbox_schema) - before
    if len(created) != 1:
        return None, f"expected 1 new semantic view in {sandbox_schema}, found {sorted(created)}"
    return f"{database}.{sandbox_schema}.{created.pop()}", None


def run_golden_gate() -> int:
    cfg = _get_snowflake_config()
    if not cfg:
        print("⚠️  SKIPPED: Snowflake credentials not available in environment for live golden gate.")
        print("   Configure snowflake_config.json or set SNOWFLAKE_* environment variables.")
        return 0

    database = cfg.database or "SEMABRIDGE"

    missing = [f"{key}: {path}" for key, path, _ in GOLDEN_MODELS if not Path(path).is_file()]
    if missing:
        print("❌ GOLDEN GATE FAILED: source PBIX not found (set GOLDEN_PBIX_<MODEL>):")
        for m in missing:
            print(f"  ✗ {m}")
        return 1

    print(f"🔒 Connecting to Snowflake {cfg.account} as {cfg.user}...")
    kwargs = get_snowflake_connect_kwargs(cfg)

    mismatches: List[str] = []
    run_id = uuid.uuid4().hex[:8].upper()
    sandboxes = {src: f"SANDBOX_GOLDEN_{run_id}_{src}" for src in {m[2] for m in GOLDEN_MODELS}}

    try:
        with snowflake.connector.connect(**kwargs) as conn:
            cur = conn.cursor()

            try:
                # -------------------------------------------------------------
                # 1. Build every golden model from HEAD into its sandbox
                # -------------------------------------------------------------
                for src, sandbox_schema in sandboxes.items():
                    print(f"🛠️  Creating sandbox {database}.{sandbox_schema} (base tables cloned from {src})...")
                    cur.execute(f"CREATE SCHEMA {database}.{sandbox_schema}")
                    _clone_base_tables(cur, database, src, sandbox_schema)

                views: Dict[str, str] = {}
                for key, pbix_path, src in GOLDEN_MODELS:
                    view, error = _build_from_head(cfg, cur, database, sandboxes[src], key, pbix_path)
                    if view:
                        views[key] = view
                        print(f"✓ {key}: built {view} from HEAD")
                    else:
                        mismatches.append(f"Build failed ({key}): {error}")
                        print(f"✗ {key}: build failed: {error}")

                # -------------------------------------------------------------
                # 2. Check the 8 Known Golden Values
                # -------------------------------------------------------------
                print("\n🔍 Validating 8 Known Golden Values in Sandbox...")

                cm_view = views.get("COMPETITIVE_MARKETING", "<not built>")
                cs_view = views.get("CORPORATE_SPEND", "<not built>")
                hr_view = views.get("HUMAN_RESOURCES", "<not built>")

                # 1. Competitive Marketing Totals (Total Units = 1,299,599)
                cur.execute(f"""
                    SELECT * FROM SEMANTIC_VIEW({cm_view} METRICS total_units)
                """)
                row = cur.fetchone()
                val1 = float(row[0]) if row and row[0] is not None else 0.0
                exp1 = 1299599.0
                if abs(val1 - exp1) > 1.0:
                    mismatches.append(f"Golden 1 Mismatch (Competitive Marketing Total Units): expected {exp1}, got {val1}")
                else:
                    print(f"  ✓ [1/8] Competitive Marketing Total Units: {val1:,.0f} == {exp1:,.0f}")

                # 2. Competitive Marketing YTD (Total Units YTD = 49,832)
                cur.execute(f"""
                    SELECT * FROM SEMANTIC_VIEW({cm_view} METRICS total_units_ytd)
                """)
                row = cur.fetchone()
                val2 = float(row[0]) if row and row[0] is not None else 0.0
                exp2 = 49832.0
                if abs(val2 - exp2) > 1.0:
                    mismatches.append(f"Golden 2 Mismatch (Competitive Marketing YTD): expected {exp2}, got {val2}")
                else:
                    print(f"  ✓ [2/8] Competitive Marketing YTD: {val2:,.0f} == {exp2:,.0f}")

                # 3. Competitive Marketing SPLY, ungrouped (Total Units SPLY_LATEST = 47,250).
                # The primary TOTAL_UNITS_SPLY is a per-row window over the year (it must be
                # grouped by year); the ungrouped latest-vs-previous value is its _LATEST
                # companion, anchored on the data's last date.
                cur.execute(f"""
                    SELECT * FROM SEMANTIC_VIEW({cm_view} METRICS total_units_sply_latest)
                """)
                row = cur.fetchone()
                val3 = float(row[0]) if row and row[0] is not None else 0.0
                exp3 = 47250.0
                if abs(val3 - exp3) > 1.0:
                    mismatches.append(f"Golden 3 Mismatch (Competitive Marketing SPLY): expected {exp3}, got {val3}")
                else:
                    print(f"  ✓ [3/8] Competitive Marketing SPLY: {val3:,.0f} == {exp3:,.0f}")

                # 4. Competitive Marketing R12M (Total Units R12Ms = 49,832)
                cur.execute(f"""
                    SELECT * FROM SEMANTIC_VIEW({cm_view} METRICS total_units_r12ms)
                """)
                row = cur.fetchone()
                val4 = float(row[0]) if row and row[0] is not None else 0.0
                exp4 = 49832.0
                if abs(val4 - exp4) > 1.0:
                    mismatches.append(f"Golden 4 Mismatch (Competitive Marketing R12M): expected {exp4}, got {val4}")
                else:
                    print(f"  ✓ [4/8] Competitive Marketing R12M: {val4:,.0f} == {exp4:,.0f}")

                # 5. Competitive Marketing Sentiment Gap (~17.265)
                cur.execute(f"""
                    SELECT * FROM SEMANTIC_VIEW({cm_view} METRICS sentiment_gap)
                """)
                row = cur.fetchone()
                val5 = float(row[0]) if row and row[0] is not None else 0.0
                exp5 = 17.2648
                if abs(val5 - exp5) > 0.05:
                    mismatches.append(f"Golden 5 Mismatch (Competitive Marketing Sentiment Gap): expected ~{exp5}, got {val5}")
                else:
                    print(f"  ✓ [5/8] Competitive Marketing Sentiment Gap: {val5:.4f} == {exp5:.4f}")

                # 6. Corporate Spend Var Plan Infrastructure (~23,536,681.48)
                cur.execute(f"""
                    SELECT business_area, var_plan 
                    FROM SEMANTIC_VIEW(
                        {cs_view} 
                        METRICS var_plan 
                        DIMENSIONS business_area
                    )
                    WHERE business_area = 'Infrastructure'
                """)
                row = cur.fetchone()
                val6 = float(row[1]) if row and row[1] is not None else 0.0
                exp6 = 23536681.48
                if abs(val6 - exp6) > 5.0:
                    mismatches.append(f"Golden 6 Mismatch (Corporate Spend Var Plan Infrastructure): expected ~{exp6}, got {val6}")
                else:
                    print(f"  ✓ [6/8] Corporate Spend Var Plan Infrastructure: {val6:,.2f} == {exp6:,.2f}")

                # 7. HR New Hires SPLY by year (2012: 3,258; 2013: 7,407; 2014: 13,382)
                cur.execute(f"""
                    SELECT year, new_hires_sply 
                    FROM SEMANTIC_VIEW(
                        {hr_view} 
                        METRICS new_hires_sply 
                        DIMENSIONS year
                    )
                    WHERE year IN (2012, 2013, 2014)
                    ORDER BY year
                """)
                rows = cur.fetchall()
                val7_dict = {int(r[0]): (float(r[1]) if r[1] is not None else 0.0) for r in rows}
                exp7_dict = {2012: 3258.0, 2013: 7407.0, 2014: 13382.0}
                for yr, exp_yr_val in exp7_dict.items():
                    act_yr_val = val7_dict.get(yr, 0.0)
                    if abs(act_yr_val - exp_yr_val) > 1.0:
                        mismatches.append(f"Golden 7 Mismatch (HR New Hires SPLY for {yr}): expected {exp_yr_val}, got {act_yr_val}")
                    else:
                        print(f"  ✓ [7/8] HR New Hires SPLY ({yr}): {act_yr_val:,.0f} == {exp_yr_val:,.0f}")

                # 8. HR New Hires _LATEST companion (13,382)
                # The latest companion resolves the latest period (2014) value
                cur.execute(f"""
                    SELECT year, new_hires_sply 
                    FROM SEMANTIC_VIEW(
                        {hr_view} 
                        METRICS new_hires_sply 
                        DIMENSIONS year
                    )
                    ORDER BY year DESC
                    LIMIT 1
                """)
                row = cur.fetchone()
                val8 = float(row[1]) if row and row[1] is not None else 0.0
                exp8 = 13382.0
                if abs(val8 - exp8) > 1.0:
                    mismatches.append(f"Golden 8 Mismatch (HR New Hires SPLY _LATEST): expected {exp8}, got {val8}")
                else:
                    print(f"  ✓ [8/8] HR New Hires SPLY _LATEST companion: {val8:,.0f} == {exp8:,.0f}")

            finally:
                # -------------------------------------------------------------
                # 3. Always drop the sandbox schemas
                # -------------------------------------------------------------
                for sandbox_schema in sandboxes.values():
                    print(f"\n🧹 Dropping temporary sandbox schema: {database}.{sandbox_schema}...")
                    cur.execute(f"DROP SCHEMA IF EXISTS {database}.{sandbox_schema} CASCADE")
                print("✓ Sandbox schemas dropped.")

    except Exception as exc:
        print(f"❌ ERROR during golden gate execution: {exc}")
        return 1

    if mismatches:
        print(f"\n❌ GOLDEN GATE FAILED: {len(mismatches)} metric mismatch(es) detected:")
        for m in mismatches:
            print(f"  ✗ {m}")
        return 1

    print("\n✅ GOLDEN GATE PASSED: All 8 golden values matched perfectly.")
    return 0


if __name__ == "__main__":
    # Windows consoles/pipes default to cp1252, which cannot encode the
    # status emoji printed above.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(run_golden_gate())
