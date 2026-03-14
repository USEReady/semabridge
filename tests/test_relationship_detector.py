"""
Tests for relationship detector.
"""

import pytest
from semabridge.connectors.relationship_detector import RelationshipDetector


class TestRelationshipDetector:
    """Tests for relationship detection."""
    
    @pytest.fixture
    def sample_tables(self):
        return {
            "customers": {"name": "customers"},
            "orders": {"name": "orders"},
            "products": {"name": "products"},
            "order_items": {"name": "order_items"},
        }
    
    @pytest.fixture
    def sample_columns(self):
        return {
            "customers": [
                {"name": "id", "data_type": "INTEGER"},
                {"name": "name", "data_type": "VARCHAR"},
            ],
            "orders": [
                {"name": "id", "data_type": "INTEGER"},
                {"name": "customer_id", "data_type": "INTEGER"},
                {"name": "order_date", "data_type": "DATE"},
            ],
            "products": [
                {"name": "id", "data_type": "INTEGER"},
                {"name": "name", "data_type": "VARCHAR"},
                {"name": "price", "data_type": "DECIMAL"},
            ],
            "order_items": [
                {"name": "id", "data_type": "INTEGER"},
                {"name": "order_id", "data_type": "INTEGER"},
                {"name": "product_id", "data_type": "INTEGER"},
                {"name": "quantity", "data_type": "INTEGER"},
            ],
        }
    
    @pytest.fixture
    def sample_pks(self):
        return {
            "customers": ["id"],
            "orders": ["id"],
            "products": ["id"],
            "order_items": ["id"],
        }
    
    def test_detect_explicit_fk(self, sample_tables, sample_columns, sample_pks):
        explicit_fks = [
            {
                "name": "fk_orders_customers",
                "from_table": "orders",
                "from_column": "customer_id",
                "to_table": "customers",
                "to_column": "id",
            }
        ]
        
        detector = RelationshipDetector(
            tables=sample_tables,
            columns=sample_columns,
            primary_keys=sample_pks,
            explicit_fks=explicit_fks,
        )
        
        relationships = detector.detect_all()
        
        # Should include explicit FK
        explicit_rel = [r for r in relationships if r["source"] == "explicit_fk"]
        assert len(explicit_rel) == 1
        assert explicit_rel[0]["from_table"] == "orders"
        assert explicit_rel[0]["to_table"] == "customers"
    
    def test_detect_by_naming_convention(self, sample_tables, sample_columns, sample_pks):
        detector = RelationshipDetector(
            tables=sample_tables,
            columns=sample_columns,
            primary_keys=sample_pks,
        )
        
        relationships = detector.detect_all()
        
        # Should detect orders.customer_id -> customers.id
        order_customer = [r for r in relationships 
                         if r["from_table"] == "orders" and r["from_column"] == "customer_id"]
        assert len(order_customer) >= 1
        
        # Should detect order_items.order_id -> orders.id
        item_order = [r for r in relationships 
                      if r["from_table"] == "order_items" and r["from_column"] == "order_id"]
        assert len(item_order) >= 1
    
    def test_no_duplicate_relationships(self, sample_tables, sample_columns, sample_pks):
        explicit_fks = [
            {
                "name": "fk_orders_customers",
                "from_table": "orders",
                "from_column": "customer_id",
                "to_table": "customers",
                "to_column": "id",
            }
        ]
        
        detector = RelationshipDetector(
            tables=sample_tables,
            columns=sample_columns,
            primary_keys=sample_pks,
            explicit_fks=explicit_fks,
        )
        
        relationships = detector.detect_all()
        
        # Should not have duplicates
        seen = set()
        for r in relationships:
            key = (r["from_table"], r["from_column"], r["to_table"], r["to_column"])
            assert key not in seen, f"Duplicate relationship: {key}"
            seen.add(key)
