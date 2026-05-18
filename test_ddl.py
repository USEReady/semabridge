import json
import sys
import logging
logging.basicConfig(level=logging.DEBUG)

from semabridge.intermediate.models import SMLModel, SMLDataset, SMLRelationship, SMLColumn
from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder
from semabridge.utils.identifier_normalizer import IdentifierSanitizer

class Behavior:
    class snowflake:
        pk_resolution_mode = "strict"
behavior = Behavior()

sml = SMLModel(unique_name="TEST")
fact = SMLDataset(unique_name="Fact")
fact.columns = [SMLColumn(unique_name="Customer Key")]
customer = SMLDataset(unique_name="Customer")
customer.columns = [SMLColumn(unique_name="Customer", is_key=True)]
sml.datasets = [fact, customer]

rel = SMLRelationship(
    unique_name="REL1",
    from_dataset="Fact",
    to_dataset="Customer",
    from_columns=["Customer Key"],
    to_columns=["Customer"]
)
sml.relationships = [rel]

registry = type('Registry', (), {'get_alias': lambda s, n: None, 'used_table_aliases': set(), 'register_dataset_alias': lambda s, n, a: None, 'dataset_aliases': {'Fact': 'FACT', 'Customer': 'CUSTOMER'}})()
schema_mgr = type('SchemaManager', (), {'_resolve_physical_column_name': lambda s, ds, c: c.upper().replace(' ', '_'), '_collect_physical_source_columns': lambda s, ds: {c.unique_name: c for c in ds.columns}})()
builder = RelationshipsClauseBuilder(IdentifierSanitizer(), schema_mgr, behavior)

ds_lookup = {'Fact': {'CUSTOMER_KEY'}, 'Customer': {'CUSTOMER'}}
ds_by_name = {'Fact': fact, 'Customer': customer}
declared_pk = {'CUSTOMER': ['CUSTOMER']}
target_alias = {}

lines = builder.build_for_sml(sml, registry.dataset_aliases, ds_by_name, ds_lookup, declared_pk, target_alias)
print("Result:", lines)
