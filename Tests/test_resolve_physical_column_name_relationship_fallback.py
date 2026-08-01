"""Regression tests for _resolve_physical_column_name's relationship-based
fallback.

When a dataset's own live Snowflake schema is unavailable, the function fell
back to sanitizing this same dataset's own declared column name/label
(via a hardcoded date/time guess-table, then a naive sanitize) — a
circular guess that can only ever reproduce sanitize(raw_col_name), never
anything more reliable. That's fine when the physical name happens to be
derivable from the raw name (e.g. reserved-word suppression), but not when
a dataset's own name/label for a shared join column genuinely differs from
the column's actual physical name — mirroring the real Date/DATE_DATE case
in proj-pbix-test, where the Date dataset's own declared label differs from
the physical FK column name it and SalesFact actually share.

The fix borrows the physical name from an active FK relationship partner's
own confirmed (live or modeled) schema instead — the two sides of an active
join are physically the same column by construction, so that partner's
resolution is a strictly more reliable answer than guessing from this
dataset's own name/label.

All identifiers below are synthetic placeholders.
"""
from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.sml.models import SMLDataset, SMLColumn, SMLRelationship, SMLModel, DataType


def _build_schema_manager() -> SnowflakeSchemaManager:
    config = SnowflakeConfig(
        account="test.local", user="u", password="p", warehouse="w",
        database="test_db", schema_name="test_schema", role="r",
    )
    behavior = ConnectorBehavior()
    idsan = IdentifierSanitizer(
        force_uppercase=behavior.compatibility.force_uppercase,
        always_quote=behavior.snowflake.quote_identifiers,
        suppress_reserved=behavior.compatibility.suppress_reserved_words,
    )
    return SnowflakeSchemaManager(
        config=config, behavior=behavior, identifier_sanitizer=idsan, connection_manager=None,
    )


def _model_with_mismatched_label():
    """Orders (fact) <-> Region (dimension), joined via an active FK
    relationship. Orders' own raw column name for the join key is
    'REGION_FK', and its live schema confirms that's also the real physical
    column name (a direct match, resolvable without any guessing). Region's
    own raw name for the *same* join key is the differently-spelled
    'RegionKey', with no live schema and a deliberately wrong label
    ('REGION_LABEL_WRONG') -- mirroring the real Date/DATE_DATE case where a
    dataset's own declared name/label for a shared join column differs from
    the column's actual physical name, and only the FK relationship (not the
    dataset's own name or label) reveals the truth.
    """
    orders = SMLDataset(
        unique_name="Orders",
        source_table="Orders",
        is_fact=True,
        columns=[
            SMLColumn(unique_name="REGION_FK", label="REGION_FK", data_type=DataType.STRING),
            SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
        ],
    )
    region = SMLDataset(
        unique_name="Region",
        source_table="Region",
        columns=[
            SMLColumn(unique_name="RegionKey", label="REGION_LABEL_WRONG", data_type=DataType.STRING),
        ],
    )
    rel = SMLRelationship(
        unique_name="REL_ORDERS_REGIONFK__REGION_REGIONKEY",
        from_dataset="Orders",
        from_columns=["REGION_FK"],
        to_dataset="Region",
        to_columns=["RegionKey"],
        is_active=True,
    )
    model = SMLModel(unique_name="synthetic_model", datasets=[orders, region], relationships=[rel])
    return model, orders, region


def test_fallback_borrows_physical_name_from_relationship_partner_when_own_schema_unknown():
    sm = _build_schema_manager()
    model, orders, region = _model_with_mismatched_label()

    # Orders' own live schema is confirmed; its FK column really is REGION_FK.
    sm._live_schema_metadata["ORDERS"] = {"REGION_FK", "AMOUNT"}
    # Region has no live schema entry at all -- the exact condition that
    # produces "no live schema was available for dataset 'Region'".

    resolved = sm._resolve_physical_column_name(region, "RegionKey", model=model)

    assert resolved == "REGION_FK", (
        "must borrow the confirmed physical name from the FK relationship "
        "partner (Orders) rather than guessing from Region's own label/name"
    )
    # Prove it actually differs from what the old behavior would have produced.
    assert resolved != "REGION_LABEL_WRONG", "must not fall back to the dataset's own (possibly wrong) label"
    assert resolved != "REGIONKEY", "must not fall back to a naive sanitize of the raw column name either"


def test_fallback_prefers_own_live_schema_when_available():
    """When the dataset's own live schema IS available, it must still take
    priority over relationship-borrowing -- the fix is an additional
    fallback layer, not a replacement for the existing, more direct checks."""
    sm = _build_schema_manager()
    model, orders, region = _model_with_mismatched_label()

    sm._live_schema_metadata["ORDERS"] = {"REGION_FK", "AMOUNT"}
    # Region's own live schema is now confirmed too, and it directly
    # contains a matching entry for the raw name -- the exact-match check
    # (steps 1/2, unchanged by this fix) must win over relationship
    # borrowing, which should not even be consulted once schema is confirmed.
    sm._live_schema_metadata["REGION"] = {"REGIONKEY", "OTHERCOL"}

    resolved = sm._resolve_physical_column_name(region, "RegionKey", model=model)
    assert resolved == "REGIONKEY"


def test_without_model_argument_falls_back_to_previous_naive_behavior():
    """Backward compatibility: callers that don't pass model= (the vast
    majority, unchanged by this fix) must see identical behavior to before."""
    sm = _build_schema_manager()
    model, orders, region = _model_with_mismatched_label()

    sm._live_schema_metadata["ORDERS"] = {"REGION_FK", "AMOUNT"}
    # No live schema for Region, and no model passed -- relationship lookup
    # cannot run, so this must fall through to the pre-existing behavior.
    resolved = sm._resolve_physical_column_name(region, "RegionKey")
    assert resolved != "REGION_FK", "without model=, relationship borrowing must not happen"
