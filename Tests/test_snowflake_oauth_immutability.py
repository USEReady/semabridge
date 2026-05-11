"""Regression test: Snowflake OAuth config immutability.

This test verifies that _acquire_oauth_token does NOT mutate the config object,
which was a critical thread-safety violation that could cause race conditions
in concurrent authentication scenarios.
"""
import sys
sys.path.insert(0, 'src')

from unittest.mock import Mock, patch
from pydantic import SecretStr
from semabridge.core.settings import SnowflakeConfig
from semabridge.connectors.snowflake_connection import _acquire_oauth_token


def test_oauth_token_does_not_mutate_config():
    """Verify that _acquire_oauth_token does NOT mutate config object."""
    
    # Create a config WITHOUT oauth_token_endpoint and oauth_scope
    config = SnowflakeConfig(
        account="test-account",
        user="testuser",
        database="testdb",
        schema_name="testschema",
        warehouse="compute",
        auth_type="oauth",
        oauth_client_id="client-id",
        oauth_client_secret=SecretStr("client-secret"),
        # Note: oauth_token_endpoint is NOT set
        # Note: oauth_scope is NOT set
    )
    
    # Capture their original values (should be None)
    original_endpoint = config.oauth_token_endpoint
    original_scope = config.oauth_scope
    
    # Mock the token endpoint response
    mock_response = Mock()
    mock_response.json.return_value = {
        "access_token": "test-token-12345",
        "expires_in": 3600
    }
    mock_response.raise_for_status.return_value = None
    
    # Attempt to acquire token (will fail at network layer, but that's ok)
    with patch('semabridge.connectors.snowflake_connection._requests.post') as mock_post:
        mock_post.return_value = mock_response
        try:
            token = _acquire_oauth_token(config)
            assert token == "test-token-12345", f"Expected token, got {token}"
        except Exception as e:
            print(f"Token acquisition failed: {e}")
            raise
    
    # Verify that config was NOT mutated
    assert config.oauth_token_endpoint == original_endpoint, \
        f"BUG: oauth_token_endpoint was mutated from {original_endpoint} to {config.oauth_token_endpoint}"
    assert config.oauth_scope == original_scope, \
        f"BUG: oauth_scope was mutated from {original_scope} to {config.oauth_scope}"
    
    print("✓ PASS: Config immutability verified - no mutations occurred")
    print(f"  - oauth_token_endpoint: {original_endpoint} (unchanged)")
    print(f"  - oauth_scope: {original_scope} (unchanged)")
    print(f"  - Token acquired: {token[:20]}...")


def test_concurrent_oauth_calls_no_state_pollution():
    """Verify that concurrent OAuth calls don't pollute each other's state."""
    import threading
    
    config1 = SnowflakeConfig(
        account="account1",
        user="user1",
        database="db1",
        schema_name="schema1",
        warehouse="wh1",
        auth_type="oauth",
        oauth_client_id="client-id-1",
        oauth_client_secret=SecretStr("secret1"),
    )
    
    config2 = SnowflakeConfig(
        account="account2",
        user="user2",
        database="db2",
        schema_name="schema2",
        warehouse="wh2",
        auth_type="oauth",
        oauth_client_id="client-id-2",
        oauth_client_secret=SecretStr("secret2"),
    )
    
    results = {}
    errors = []
    
    def acquire_token(config, config_id):
        try:
            mock_response = Mock()
            mock_response.json.return_value = {
                "access_token": f"token-{config_id}",
                "expires_in": 3600
            }
            mock_response.raise_for_status.return_value = None
            
            with patch('semabridge.connectors.snowflake_connection._requests.post') as mock_post:
                mock_post.return_value = mock_response
                token = _acquire_oauth_token(config)
                results[config_id] = {
                    'token': token,
                    'endpoint': config.oauth_token_endpoint,
                    'scope': config.oauth_scope,
                }
        except Exception as e:
            errors.append((config_id, str(e)))
    
    # Run concurrent token acquisitions
    t1 = threading.Thread(target=acquire_token, args=(config1, 'config1'))
    t2 = threading.Thread(target=acquire_token, args=(config2, 'config2'))
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    if errors:
        print(f"✗ FAIL: Errors during concurrent execution: {errors}")
        raise RuntimeError(f"Concurrent execution failed: {errors}")
    
    # Verify no state pollution
    assert results['config1']['token'] == 'token-config1', "Config1 token incorrect"
    assert results['config2']['token'] == 'token-config2', "Config2 token incorrect"
    assert results['config1']['endpoint'] is None, "Config1 endpoint was mutated"
    assert results['config2']['endpoint'] is None, "Config2 endpoint was mutated"
    
    print("✓ PASS: Concurrent execution - no state pollution detected")
    print(f"  - Config1 token: {results['config1']['token']}")
    print(f"  - Config2 token: {results['config2']['token']}")
    print(f"  - Config endpoints remained None (no mutation)")


if __name__ == '__main__':
    print("=" * 70)
    print("REGRESSION TEST: Snowflake OAuth Config Immutability")
    print("=" * 70)
    
    print("\n[Test 1] OAuth does not mutate config...")
    try:
        test_oauth_token_does_not_mutate_config()
    except AssertionError as e:
        print(f"✗ FAIL: {e}")
        sys.exit(1)
    
    print("\n[Test 2] Concurrent OAuth calls don't pollute state...")
    try:
        test_concurrent_oauth_calls_no_state_pollution()
    except AssertionError as e:
        print(f"✗ FAIL: {e}")
        sys.exit(1)
    
    print("\n" + "=" * 70)
    print("ALL REGRESSION TESTS PASSED")
    print("=" * 70)
