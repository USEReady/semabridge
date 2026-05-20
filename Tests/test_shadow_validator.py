import base64
from semabridge.adapters.shadow_validator import compare_tom_and_fallback
from semabridge.core.settings import FabricConfig


def _make_part(path: str, text: str):
    payload = base64.b64encode(text.encode('utf-8')).decode('ascii')
    return {"path": path, "payloadType": "InlineBase64", "payload": payload}


def test_shadow_validator_parity(monkeypatch):
    parts = [
        _make_part('definition/tables/Customers.tmdl', "table 'Customers'\ncolumn 'CustomerID'"),
        _make_part('definition/tables/Orders.tmdl', "table 'Orders'\ncolumn 'CustomerID'"),
        _make_part('definition/relationships.tmdl', "relationship 'Customers'[CustomerID] -> 'Orders'[CustomerID]"),
    ]

    # TOM returns equivalent mapping
    tom_model = {
        'model': {
            'relationships': [
                {
                    'fromTable': 'Customers', 'fromColumn': 'CustomerID',
                    'toTable': 'Orders', 'toColumn': 'CustomerID'
                }
            ]
        }
    }

    def fake_tom(parts_arg):
        return tom_model

    monkeypatch.setattr('semabridge.adapters.tom_integration.parse_tmdl_with_tom', fake_tom)

    res = compare_tom_and_fallback(parts, FabricConfig(tenant_id='t', client_id='c', workspace_id='w'))
    assert res['parity'] is True
    assert res['tom_available'] is True


def test_shadow_validator_discrepancy(monkeypatch):
    parts = [
        _make_part('definition/tables/Customers.tmdl', "table 'Customers'\ncolumn 'CustomerID'\ncolumn 'CustomerCode'"),
        _make_part('definition/tables/Orders.tmdl', "table 'Orders'\ncolumn 'CustomerKey'"),
        _make_part('definition/relationships.tmdl', "relationship 'Customers'[CustomerID] -> 'Orders'[CustomerKey]"),
    ]

    tom_model = {
        'model': {
            'relationships': [
                {
                    'fromTable': 'Customers', 'fromColumn': 'CustomerCode',
                    'toTable': 'Orders', 'toColumn': 'CustomerKey'
                }
            ]
        }
    }

    def fake_tom(parts_arg):
        return tom_model

    monkeypatch.setattr('semabridge.adapters.tom_integration.parse_tmdl_with_tom', fake_tom)

    res = compare_tom_and_fallback(parts, FabricConfig(tenant_id='t', client_id='c', workspace_id='w'))
    assert res['parity'] is False
    assert res['tom_available'] is True
    assert len(res['only_in_tom']) >= 1 or len(res['only_in_fallback']) >= 1
