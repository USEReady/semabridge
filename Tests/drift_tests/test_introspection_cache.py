"""Tests for Introspection Cache (governance.drift.introspection_cache)."""

import time

from semabridge.governance.drift.introspection_cache import (
    IntrospectionCache,
    derive_connection_identity,
    invalidate_cached_definition,
    clear_cache,
)
from semabridge.governance.drift.adapters.base import PlatformAdapter
from semabridge.governance.drift.models import ViewDefinition, ObjectDef, ObjectKind


class DummyTestAdapter(PlatformAdapter):
    platform_name = "MOCK_DB"

    def __init__(self, fetch_callback=None):
        self.fetch_callback = fetch_callback or (lambda v: f"DDL FOR {v}")
        self.fetch_count = 0

    def list_views(self, project_id: str, scope=None):
        return ["SCHEMA.VIEW_A"]

    def fetch_definition_text(self, view_name: str, connection_config=None) -> str:
        self.fetch_count += 1
        return self.fetch_callback(view_name)

    def parse_definition(self, raw_text: str, view_name: str, source: str = "live", descriptions=None):
        return ViewDefinition(
            view_name=view_name,
            platform=self.platform_name,
            source=source,
            retrieved_at="2026-11-23T00:00:00Z",
            objects=[
                ObjectDef(kind=ObjectKind.METRIC, table="VIEW_A", name="M1", expression="SUM(X)"),
            ],
            raw_definition_text=raw_text,
        )


def test_derive_connection_identity():
    assert derive_connection_identity(None) == "default"
    assert derive_connection_identity({}) == "default"
    
    cfg = {
        "account": "TEST_ACCT",
        "user": "TEST_USER",
        "database": "TEST_DB",
    }
    ident = derive_connection_identity(cfg)
    assert "test_acct" in ident
    assert "test_user" in ident
    assert "test_db" in ident


def test_cache_miss_hit_and_clear():
    cache = IntrospectionCache(ttl_seconds=60, max_size=10)
    
    assert cache.get("SNOWFLAKE", "id1", "DB.SCH.V1") is None
    
    cache.set("SNOWFLAKE", "id1", "DB.SCH.V1", "CREATE SEMANTIC VIEW V1...")
    
    hit = cache.get("SNOWFLAKE", "id1", "DB.SCH.V1")
    assert hit == "CREATE SEMANTIC VIEW V1..."
    
    # Case insensitive view lookup
    hit_lower = cache.get("SNOWFLAKE", "id1", "db.sch.v1")
    assert hit_lower == "CREATE SEMANTIC VIEW V1..."
    
    # Force refresh bypasses
    assert cache.get("SNOWFLAKE", "id1", "DB.SCH.V1", force_refresh=True) is None
    
    cache.clear()
    assert cache.size() == 0
    assert cache.get("SNOWFLAKE", "id1", "DB.SCH.V1") is None


def test_cache_ttl_expiration():
    # Cache with 1 second TTL
    cache = IntrospectionCache(ttl_seconds=1, max_size=10)
    cache.set("DATABRICKS", "id1", "CAT.SCH.V1", "YAML TEXT")
    
    assert cache.get("DATABRICKS", "id1", "CAT.SCH.V1") == "YAML TEXT"
    
    # Wait for expiration
    time.sleep(1.1)
    assert cache.get("DATABRICKS", "id1", "CAT.SCH.V1") is None


def test_cache_bounded_lru_eviction():
    cache = IntrospectionCache(ttl_seconds=60, max_size=3)
    
    cache.set("SNOWFLAKE", "id1", "VIEW_1", "TEXT 1")
    cache.set("SNOWFLAKE", "id1", "VIEW_2", "TEXT 2")
    cache.set("SNOWFLAKE", "id1", "VIEW_3", "TEXT 3")
    assert cache.size() == 3
    
    # Access VIEW_1 so VIEW_2 becomes the oldest
    assert cache.get("SNOWFLAKE", "id1", "VIEW_1") == "TEXT 1"
    
    # Insert 4th view -> VIEW_2 should be evicted
    cache.set("SNOWFLAKE", "id1", "VIEW_4", "TEXT 4")
    assert cache.size() == 3
    
    assert cache.get("SNOWFLAKE", "id1", "VIEW_1") == "TEXT 1"
    assert cache.get("SNOWFLAKE", "id1", "VIEW_2") is None  # Evicted
    assert cache.get("SNOWFLAKE", "id1", "VIEW_3") == "TEXT 3"
    assert cache.get("SNOWFLAKE", "id1", "VIEW_4") == "TEXT 4"


def test_cache_invalidation_per_view():
    cache = IntrospectionCache(ttl_seconds=60, max_size=10)
    
    cache.set("SNOWFLAKE", "ident_a", "SALES_VIEW", "SNOW DDL")
    cache.set("DATABRICKS", "ident_b", "SALES_VIEW", "DATABRICKS YAML")
    cache.set("SNOWFLAKE", "ident_a", "OTHER_VIEW", "OTHER DDL")
    
    # Invalidate only Snowflake SALES_VIEW
    count = cache.invalidate("SNOWFLAKE", "sales_view")
    assert count == 1
    
    assert cache.get("SNOWFLAKE", "ident_a", "SALES_VIEW") is None
    assert cache.get("DATABRICKS", "ident_b", "SALES_VIEW") == "DATABRICKS YAML"
    assert cache.get("SNOWFLAKE", "ident_a", "OTHER_VIEW") == "OTHER DDL"


def test_platform_adapter_get_definition_uses_cache():
    clear_cache()
    adapter = DummyTestAdapter()
    
    conn_cfg = {"account": "acct", "user": "usr", "database": "db"}
    
    # Call 1: fetch called
    view_def1 = adapter.get_definition("SCHEMA.VIEW_A", connection_config=conn_cfg)
    assert adapter.fetch_count == 1
    assert view_def1.raw_definition_text == "DDL FOR SCHEMA.VIEW_A"
    
    # Call 2: cache hit, fetch not called
    view_def2 = adapter.get_definition("SCHEMA.VIEW_A", connection_config=conn_cfg)
    assert adapter.fetch_count == 1
    assert view_def2.raw_definition_text == "DDL FOR SCHEMA.VIEW_A"
    
    # Call 3: force_refresh=True -> triggers fetch
    adapter.get_definition("SCHEMA.VIEW_A", connection_config=conn_cfg, force_refresh=True)
    assert adapter.fetch_count == 2
    
    # Invalidate cache for this view
    invalidate_cached_definition("MOCK_DB", "SCHEMA.VIEW_A")
    adapter.get_definition("SCHEMA.VIEW_A", connection_config=conn_cfg)
    assert adapter.fetch_count == 3
