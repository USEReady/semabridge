"""Tests for relationship normalization in ExecutionEngine._normalize_relationships_for_target.

Verifies:
1. Distinct column-level relationships between the same table pair are preserved.
2. Relationships with alternate paths through other tables (diamond/DAG) are preserved.
3. Genuine duplicate relationships (same endpoints) are deduplicated, keeping active/strongest.
4. Actual directed cycles are detected and broken without dropping valid source relationships.
5. Relationship metadata (is_active, cardinality, cross_filter) is preserved.
"""
from semabridge.core.engine.conversion import base as _conv_base
from semabridge.converter.osi_to_sml import (
    SMLCardinality,
    SMLCrossFilterDirection,
    SMLModel,
    SMLRelationship,
)


class DummyEngine:
    _has_path = _conv_base._has_path
    _select_strongest_relationship = _conv_base._select_strongest_relationship
    _normalize_relationships_for_target = _conv_base._normalize_relationships_for_target


def _rel(
    unique_name: str,
    from_dataset: str,
    from_column: str,
    to_dataset: str,
    to_column: str,
    is_active: bool = True,
    cardinality: SMLCardinality = SMLCardinality.MANY_TO_ONE,
    cross_filter: SMLCrossFilterDirection = SMLCrossFilterDirection.SINGLE,
) -> SMLRelationship:
    return SMLRelationship(
        unique_name=unique_name,
        from_dataset=from_dataset,
        from_columns=[from_column],
        to_dataset=to_dataset,
        to_columns=[to_column],
        is_active=is_active,
        cardinality=cardinality,
        cross_filter=cross_filter,
    )


def test_distinct_column_relationships_between_same_table_pair_preserved():
    """Distinct foreign key columns between the same pair of tables (e.g. role-playing date dims)

    must both be preserved.
    """
    model = SMLModel(
        unique_name="TestModel",
        datasets=[],
        relationships=[
            _rel("rel_order_date", "Sales", "OrderDate", "Calendar", "Date", is_active=True),
            _rel("rel_ship_date", "Sales", "ShipDate", "Calendar", "Date", is_active=False),
        ],
    )
    engine = DummyEngine()
    engine._normalize_relationships_for_target(model)

    assert len(model.relationships) == 2
    order_rel = next(r for r in model.relationships if r.from_columns == ["OrderDate"])
    ship_rel = next(r for r in model.relationships if r.from_columns == ["ShipDate"])
    assert order_rel.is_active is True
    assert ship_rel.is_active is False


def test_alternate_paths_through_other_tables_preserved():
    """Diamond / multi-hop DAG topologies (e.g. A->B, B->C, and direct A->C)

    must NOT be pruned as redundant cycles.
    """
    model = SMLModel(
        unique_name="TestDiamond",
        datasets=[],
        relationships=[
            _rel("rel_ab", "TableA", "B_ID", "TableB", "ID"),
            _rel("rel_bc", "TableB", "C_ID", "TableC", "ID"),
            _rel("rel_ac", "TableA", "C_ID", "TableC", "ID"),
        ],
    )
    engine = DummyEngine()
    engine._normalize_relationships_for_target(model)

    assert len(model.relationships) == 3
    endpoints = {(r.from_dataset, r.to_dataset) for r in model.relationships}
    assert endpoints == {("TableA", "TableB"), ("TableB", "TableC"), ("TableA", "TableC")}


def test_genuine_duplicate_endpoints_deduplicated():
    """Relationships with identical endpoints (same tables and same columns)

    are deduplicated, preferring active over inactive.
    """
    model = SMLModel(
        unique_name="TestDedup",
        datasets=[],
        relationships=[
            _rel("rel_inactive", "Orders", "CustomerID", "Customer", "ID", is_active=False),
            _rel("rel_active", "Orders", "CustomerID", "Customer", "ID", is_active=True),
        ],
    )
    engine = DummyEngine()
    engine._normalize_relationships_for_target(model)

    assert len(model.relationships) == 1
    assert model.relationships[0].is_active is True


def test_actual_directed_cycle_detected_and_broken():
    """A circular dependency (A -> B -> C -> A) must be detected and broken

    without dropping legitimate non-cyclic relationships.
    """
    model = SMLModel(
        unique_name="TestCycle",
        datasets=[],
        relationships=[
            _rel("rel_ab", "EntityA", "B_FK", "EntityB", "ID", is_active=True),
            _rel("rel_bc", "EntityB", "C_FK", "EntityC", "ID", is_active=True),
            _rel("rel_ca", "EntityC", "A_FK", "EntityA", "ID", is_active=False),
            _rel("rel_cd", "EntityC", "D_FK", "EntityD", "ID", is_active=True),
        ],
    )
    engine = DummyEngine()
    engine._normalize_relationships_for_target(model)

    # The 3-node cycle EntityA -> EntityB -> EntityC -> EntityA should be broken by dropping C->A
    # EntityC -> EntityD is valid and must survive
    assert len(model.relationships) == 3
    endpoints = {(r.from_dataset, r.to_dataset) for r in model.relationships}
    assert ("EntityC", "EntityA") not in endpoints
    assert ("EntityC", "EntityD") in endpoints


def test_self_referencing_relationship_detected():
    """A self-referencing relationship (A -> A) is detected and removed."""
    model = SMLModel(
        unique_name="TestSelfRef",
        datasets=[],
        relationships=[
            _rel("rel_self", "Employee", "ManagerID", "Employee", "ID", is_active=True),
            _rel("rel_dept", "Employee", "DeptID", "Department", "ID", is_active=True),
        ],
    )
    engine = DummyEngine()
    engine._normalize_relationships_for_target(model)

    assert len(model.relationships) == 1
    assert model.relationships[0].to_dataset == "Department"


def test_relationship_metadata_preservation():
    """Cardinality, cross-filter direction, and active status are preserved."""
    model = SMLModel(
        unique_name="TestMetadata",
        datasets=[],
        relationships=[
            _rel(
                "rel_1to1",
                "Customer",
                "ID",
                "Sales",
                "ID",
                is_active=True,
                cardinality=SMLCardinality.ONE_TO_ONE,
                cross_filter=SMLCrossFilterDirection.BOTH,
            ),
            _rel(
                "rel_mto1",
                "Product",
                "Product",
                "AssociatedProduct",
                "Product",
                is_active=False,
                cardinality=SMLCardinality.MANY_TO_ONE,
                cross_filter=SMLCrossFilterDirection.SINGLE,
            ),
        ],
    )
    engine = DummyEngine()
    engine._normalize_relationships_for_target(model)

    assert len(model.relationships) == 2
    rel1 = next(r for r in model.relationships if r.from_dataset == "Customer")
    assert rel1.cardinality == SMLCardinality.ONE_TO_ONE
    assert rel1.cross_filter == SMLCrossFilterDirection.BOTH
    assert rel1.is_active is True

    rel2 = next(r for r in model.relationships if r.from_dataset == "Product")
    assert rel2.cardinality == SMLCardinality.MANY_TO_ONE
    assert rel2.cross_filter == SMLCrossFilterDirection.SINGLE
    assert rel2.is_active is False
