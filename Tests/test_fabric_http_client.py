from __future__ import annotations

import email.utils
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from semabridge.sources.fabric.http_client import (
    FabricHttpClient,
    get_cloud_endpoints,
    get_fabric_api_base,
    get_powerbi_api_base,
    parse_retry_after,
)


def test_parse_retry_after_integer_seconds():
    assert parse_retry_after("15") == 15.0
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("-5") == 0.0


def test_parse_retry_after_http_date():
    target_time = datetime.now(timezone.utc) + timedelta(seconds=25)
    date_str = email.utils.format_datetime(target_time)
    parsed = parse_retry_after(date_str)
    # Within 2 seconds of 25.0
    assert 20.0 <= parsed <= 30.0


def test_parse_retry_after_fallback():
    assert parse_retry_after(None, default_delay=5.0) == 5.0
    assert parse_retry_after("invalid_header", default_delay=3.5) == 3.5


def test_sovereign_cloud_endpoints():
    # Public
    pub = get_cloud_endpoints("public")
    assert pub["fabric_api"] == "https://api.fabric.microsoft.com/v1"
    assert pub["powerbi_api"] == "https://api.powerbi.com/v1.0/myorg"

    # GCC High
    gcchigh = get_cloud_endpoints("gcchigh")
    assert "high.powerbigov.us" in gcchigh["powerbi_api"]
    assert "fabric.microsoft.us" in gcchigh["fabric_api"]

    # China
    cn = get_cloud_endpoints("china")
    assert "powerbi.cn" in cn["powerbi_api"]
    assert "fabric.microsoft.cn" in cn["fabric_api"]

    # DoD
    dod = get_cloud_endpoints("dod")
    assert "mil.powerbigov.us" in dod["powerbi_api"]

    # Germany
    de = get_cloud_endpoints("germany")
    assert "powerbi.de" in de["powerbi_api"]


def test_fabric_http_client_429_retry_after():
    mock_session = MagicMock()

    # First response: 429 with Retry-After: 3
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "3"}

    # Second response: 200 OK
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"value": "success"}

    mock_session.request.side_effect = [resp_429, resp_200]

    client = FabricHttpClient(session=mock_session, default_retry_delay=1.0)

    with patch("time.sleep") as mock_sleep:
        res = client.request("GET", "https://api.fabric.microsoft.com/v1/workspaces")
        assert res.status_code == 200
        # Assert sleep was called with Retry-After duration (3.0)
        mock_sleep.assert_called_once_with(3.0)
        assert mock_session.request.call_count == 2

