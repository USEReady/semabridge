"""Extraction-stage drop reasons are written once (Snowflake wording) and
adapted for a Databricks run. None of them may still name Snowflake."""
import pytest

from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.core.target_rules import adapt_target_message

REASONS = [
    "field parameter — Power BI report-time field switcher defined in DAX; "
    "Snowflake semantic views have no field parameters, so it is not migrated",
    "calculated table — not materialized in Snowflake; materialize it as a table/view to include it",
    "calculation group — Snowflake semantic views have no calculation groups; 2 calculation item(s) not migrated",
    "sort order of 'MONTH' by 'MONTH NUMBER' not carried — Snowflake semantic views have no sort-by-column; "
    "order by 'MONTH NUMBER' explicitly in queries",
    "hierarchy 'Geo' (Country > City) not migrated as a drill path — Snowflake semantic views have no "
    "hierarchy object; its level columns are still emitted as individual dimensions",
    "row-level security role 'EU' not migrated — Snowflake semantic views do not carry RLS; implement with a "
    "Snowflake row access policy. Filter: Region = 'EU'. Object-level security not migrated: Sales[Margin]"
    " — implement with Snowflake masking policies/grants",
    "perspective 'Exec' not migrated — Snowflake semantic views have no perspectives",
]


@pytest.mark.parametrize("reason", REASONS)
def test_databricks_ledger_never_says_snowflake(reason):
    ledger = DropLedger(target_type="databricks")
    ledger.record("table", "X", DropStage.EXTRACTION, reason, by_design=True)

    recorded = ledger.to_json()[0]["reason"]
    assert "Snowflake" not in recorded
    assert "Databricks" in recorded or "Unity Catalog" in recorded


def test_security_advice_names_unity_catalog_controls():
    text = adapt_target_message(REASONS[5], "databricks")
    assert "Unity Catalog row filter" in text
    assert "Unity Catalog column masks/grants" in text


@pytest.mark.parametrize("reason", REASONS)
def test_snowflake_wording_is_unchanged_for_snowflake(reason):
    ledger = DropLedger(target_type="snowflake")
    ledger.record("table", "X", DropStage.EXTRACTION, reason, by_design=True)
    assert ledger.to_json()[0]["reason"] == reason
