"""
phase_a_adapters.py - wire the regression tests to SemaBridge's REAL code.

Fill in the two functions, then run:
    $env:SEMABRIDGE_NORMALIZER  = "phase_a_adapters:semabridge_normalize"
    $env:SEMABRIDGE_KEY_PLANNER = "phase_a_adapters:semabridge_plan_keys"   # once key resolution exists
    uv run python -m pytest phase_a\\tests -q

The tests pass synthetic relationship objects with: unique_name, from_dataset, from_columns,
to_dataset, to_columns, is_active, cardinality. Convert them to whatever SemaBridge expects.
"""


def semabridge_normalize(relationships):
    """Return the relationships SemaBridge KEEPS (or (kept, decisions))."""
    # Find the real entry point first, e.g.:
    #   Select-String -Path src -Recurse -Pattern "def _normalize_relationships"
    # Then build SMLRelationship objects from the test objects and call it, for example:
    #
    # from semabridge.sml.models import SMLRelationship, Cardinality
    # from semabridge.core.engine.conversion.base import <EngineClass>
    # sml_rels = [SMLRelationship(unique_name=r.unique_name, from_dataset=r.from_dataset,
    #             from_columns=r.from_columns, to_dataset=r.to_dataset, to_columns=r.to_columns,
    #             is_active=r.is_active, cardinality=<map r.cardinality>) for r in relationships]
    # kept = <EngineClass>._normalize_relationships(<self or instance>, sml_rels)
    # by_id = {(x.from_dataset, tuple(x.from_columns), x.to_dataset, tuple(x.to_columns)): x for x in relationships}
    # return [by_id[(k.from_dataset, tuple(k.from_columns), k.to_dataset, tuple(k.to_columns))] for k in kept]
    raise NotImplementedError("wire this to SemaBridge's _normalize_relationships")


def semabridge_plan_keys(relationships, dataset_aliases, declared_pk_by_alias, dataset_col_lookup,
                         resolve_column=lambda ds, c: c, is_unique=None, **_):
    """Return an object shaped like relationship_resolver.RelationshipPlan (keys, decisions)."""
    raise NotImplementedError("wire this once SemaBridge's key resolution exists")
