import base64
import json
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.core.settings import FabricConfig


def _make_part(path: str, text: str) -> dict:
    payload = base64.b64encode(text.encode('utf-8')).decode('ascii')
    return {"path": path, "payloadType": "InlineBase64", "payload": payload}


def _norm_rels(rels):
    """
    Normalize relationships into a set of 4-tuples: (fromTable, fromColumn, toTable, toColumn).
    We intentionally ignore cardinality/cross-filter metadata for basic parity checks.
    """
    out = set()
    for r in rels:
        ft = str(r.get('fromTable') or r.get('from_table') or r.get('from_dataset') or '').strip().casefold()
        fc = str(r.get('fromColumn') or r.get('from_column') or r.get('from_columns') or '').strip().casefold()
        tt = str(r.get('toTable') or r.get('to_table') or r.get('to_dataset') or '').strip().casefold()
        tc = str(r.get('toColumn') or r.get('to_column') or r.get('to_columns') or '').strip().casefold()
        out.add((ft, fc, tt, tc))
    return out


def test_tom_and_fallback_produce_equivalent_relationships(monkeypatch):
    # Build TMDL parts: two table files and a relationships.tmdl
    tbl_customers = """
    table 'Customers'
    column 'CustomerID'
    """
    tbl_orders = """
    table 'Orders'
    column 'CustomerID'
    """
    rels_tmdl = """
    relationship 'Customers'[CustomerID] -> 'Orders'[CustomerID]
    """

    parts = [
        _make_part('definition/tables/Customers.tmdl', tbl_customers),
        _make_part('definition/tables/Orders.tmdl', tbl_orders),
        _make_part('definition/relationships.tmdl', rels_tmdl),
    ]

    cfg = FabricConfig(tenant_id='t', client_id='c', workspace_id='w')
    extractor = FabricExtractor(cfg, access_token=None)

    # Compute fallback model
    fallback_model = extractor._parse_definition_response({'definition': {'parts': parts}})
    fallback_rels = fallback_model.get('model', {}).get('relationships', [])

    # Simulate TOM returning an equivalent model (possibly different keys)
    tom_model = {
        'model': {
            'name': 'TomModel',
            'tables': [],
            'relationships': [
                {
                    'fromTable': 'Customers',
                    'fromColumn': 'CustomerID',
                    'toTable': 'Orders',
                    'toColumn': 'CustomerID',
                    'cardinality': 'OneToMany',
                    'cross_filter': 'BothDirections',
                    'isActive': True,
                }
            ]
        }
    }

    # Monkeypatch the TOM integration to return our tom_model when called.
    def fake_parse_tmdl_with_tom(parts_arg):
        return tom_model

    monkeypatch.setattr('semabridge.adapters.tom_integration.parse_tmdl_with_tom', fake_parse_tmdl_with_tom)

    # When TOM returns a model, _parse_definition_response should prefer it
    tom_result = extractor._parse_definition_response({'definition': {'parts': parts}})
    tom_rels = tom_result.get('model', {}).get('relationships', [])

    # Normalize and compare
    assert _norm_rels(fallback_rels) == _norm_rels(tom_rels)


def test_shadow_mode_reports_discrepancy(monkeypatch):
    # Craft parts where fallback infers a different relationship than TOM
    tbl_customers = """
    table 'Customers'
    column 'CustomerID'
    column 'CustomerCode'
    """
    tbl_orders = """
    table 'Orders'
    column 'CustomerKey'
    """
    rels_tmdl = """
    # Fallback will likely infer Customers.CustomerID -> Orders.CustomerKey
    # TOM returns a different column mapping.
    relationship 'Customers'[CustomerID] -> 'Orders'[CustomerKey]
    """

    parts = [
        _make_part('definition/tables/Customers.tmdl', tbl_customers),
        _make_part('definition/tables/Orders.tmdl', tbl_orders),
        _make_part('definition/relationships.tmdl', rels_tmdl),
    ]

    cfg = FabricConfig(tenant_id='t', client_id='c', workspace_id='w')
    extractor = FabricExtractor(cfg, access_token=None)

    fallback_model = extractor._parse_definition_response({'definition': {'parts': parts}})
    fallback_rels = fallback_model.get('model', {}).get('relationships', [])

    # TOM returns a subtly different relationship to simulate discrepancy
    tom_model = {
        'model': {
            'name': 'TomModel',
            'tables': [],
            'relationships': [
                {
                    'fromTable': 'Customers',
                    'fromColumn': 'CustomerCode',  # different column
                    'toTable': 'Orders',
                    'toColumn': 'CustomerKey',
                    'cardinality': 'OneToMany',
                    'isActive': True,
                }
            ]
        }
    }

    def fake_parse_tmdl_with_tom(parts_arg):
        return tom_model

    monkeypatch.setattr('semabridge.adapters.tom_integration.parse_tmdl_with_tom', fake_parse_tmdl_with_tom)

    tom_result = extractor._parse_definition_response({'definition': {'parts': parts}})
    tom_rels = tom_result.get('model', {}).get('relationships', [])

    # The norm sets should differ to indicate discrepancy
    assert _norm_rels(fallback_rels) != _norm_rels(tom_rels)
