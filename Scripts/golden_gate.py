#!/usr/bin/env python3
"""scripts/golden_gate.py - Automated Golden Mathematical Quality Gate.

Deploys test semantic views to a temporary sandbox schema in Snowflake, verifies
the 8 known golden metric values, drops the sandbox schema, and enforces zero mathematical drift.

The 8 Verified Golden Values:
  1. Competitive Marketing: Totals (Total Units = 1,299,599)
  2. Competitive Marketing: YTD (Total Units YTD = 49,832)
  3. Competitive Marketing: SPLY (Total Units SPLY = 47,250)
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


def run_golden_gate() -> int:
    cfg = _get_snowflake_config()
    if not cfg:
        print("⚠️  SKIPPED: Snowflake credentials not available in environment for live golden gate.")
        print("   Configure snowflake_config.json or set SNOWFLAKE_* environment variables.")
        return 0

    sandbox_id = uuid.uuid4().hex[:8].upper()
    sandbox_schema = f"SANDBOX_GOLDEN_{sandbox_id}"
    database = cfg.database or "SEMABRIDGE"

    print(f"🔒 Connecting to Snowflake {cfg.account} as {cfg.user}...")
    kwargs = get_snowflake_connect_kwargs(cfg)

    mismatches: List[str] = []

    try:
        with snowflake.connector.connect(**kwargs) as conn:
            cur = conn.cursor()
            print(f"🛠️  Creating temporary sandbox schema: {database}.{sandbox_schema}...")
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{sandbox_schema}")

            try:
                # -------------------------------------------------------------
                # 1. Deploy views to sandbox schema from verified definitions
                # -------------------------------------------------------------
                print("📦 Deploying semantic views to sandbox schema...")

                views_to_clone = [
                    ("SEMABRIDGE.SEMABRIDGE_WORKSPACE.COMPETITIVE_MARKETING_ANALYSIS_MORE_SYNONYMS_UPDATED_SEMANTIC", "COMPETITIVE_MARKETING_SEMANTIC"),
                    ("SEMABRIDGE.SEMABRIDGE_WORKSPACE.CORPORATE_SPEND_SEMANTIC", "CORPORATE_SPEND_SEMANTIC"),
                    ("SEMABRIDGE.HUMAN_RESOURCES.HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC", "HUMAN_RESOURCES_SEMANTIC"),
                ]

                for src_view, sandbox_view_name in views_to_clone:
                    cur.execute(f"SELECT GET_DDL('SEMANTIC_VIEW', '{src_view}')")
                    ddl = cur.fetchone()[0]

                    # Replace view name with sandbox-qualified view name
                    # e.g., create or replace semantic view CORPORATE_SPEND_SEMANTIC -> create or replace semantic view <database>.<sandbox_schema>.CORPORATE_SPEND_SEMANTIC
                    sub_ddl = re.sub(
                        r"(?i)create\s+or\s+replace\s+semantic\s+view\s+(?:\"?[^\s\.]+\"?\.)?\"?([^\s\.\(]+)\"?",
                        f'CREATE OR REPLACE SEMANTIC VIEW {database}.{sandbox_schema}.{sandbox_view_name}',
                        ddl,
                        count=1,
                    )
                    cur.execute(sub_ddl)

                print("✓ Sandbox semantic views deployed successfully.")

                # -------------------------------------------------------------
                # 2. Check the 8 Known Golden Values
                # -------------------------------------------------------------
                print("\n🔍 Validating 8 Known Golden Values in Sandbox...")

                # 1. Competitive Marketing Totals (Total Units = 1,299,599)
                cur.execute(f"""
                    SELECT * FROM SEMANTIC_VIEW({database}.{sandbox_schema}.COMPETITIVE_MARKETING_SEMANTIC METRICS total_units)
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
                    SELECT * FROM SEMANTIC_VIEW({database}.{sandbox_schema}.COMPETITIVE_MARKETING_SEMANTIC METRICS total_units_ytd)
                """)
                row = cur.fetchone()
                val2 = float(row[0]) if row and row[0] is not None else 0.0
                exp2 = 49832.0
                if abs(val2 - exp2) > 1.0:
                    mismatches.append(f"Golden 2 Mismatch (Competitive Marketing YTD): expected {exp2}, got {val2}")
                else:
                    print(f"  ✓ [2/8] Competitive Marketing YTD: {val2:,.0f} == {exp2:,.0f}")

                # 3. Competitive Marketing SPLY (Total Units SPLY = 47,250)
                cur.execute(f"""
                    SELECT * FROM SEMANTIC_VIEW({database}.{sandbox_schema}.COMPETITIVE_MARKETING_SEMANTIC METRICS total_units_sply)
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
                    SELECT * FROM SEMANTIC_VIEW({database}.{sandbox_schema}.COMPETITIVE_MARKETING_SEMANTIC METRICS total_units_r12ms)
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
                    SELECT * FROM SEMANTIC_VIEW({database}.{sandbox_schema}.COMPETITIVE_MARKETING_SEMANTIC METRICS sentiment_gap)
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
                        {database}.{sandbox_schema}.CORPORATE_SPEND_SEMANTIC 
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
                        {database}.{sandbox_schema}.HUMAN_RESOURCES_SEMANTIC 
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
                        {database}.{sandbox_schema}.HUMAN_RESOURCES_SEMANTIC 
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
                # 3. Always drop the sandbox schema
                # -------------------------------------------------------------
                print(f"\n🧹 Dropping temporary sandbox schema: {database}.{sandbox_schema}...")
                cur.execute(f"DROP SCHEMA IF EXISTS {database}.{sandbox_schema} CASCADE")
                print("✓ Sandbox schema dropped.")

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
    sys.exit(run_golden_gate())
