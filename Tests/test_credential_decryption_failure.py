"""Tests for credential decryption failure handling.

Requirements:
- Never send ciphertext as a password or token.
- Treat undecryptable secret as missing credential and raise an error telling the user
  which connection and credential to re-enter.
- Surface this error in UI/test-connection response.
- Connector is never called with ciphertext.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet

from semabridge.core.exceptions import MissingCredentialError, RepositoryError
from semabridge.repository.credential_manager import (
    CredentialManager,
    CredentialDecryptionError,
)
from semabridge.repository.orm.models import Credential
from semabridge.api.services.connection_domain_service import test_connection as run_test_connection


@pytest.fixture
def foreign_token():
    foreign_key = Fernet.generate_key()
    return Fernet(foreign_key).encrypt(b"secret_unrecoverable").decode("utf-8")


def test_get_credentials_undecryptable_secret_raises_reenter_error(foreign_token):
    cm = CredentialManager(url_override="sqlite:///:memory:")
    with cm._session() as session:
        session.add(Credential(owner_id=0, service="snowflake", key="account", value="my_account", is_secret=False))
        session.add(Credential(owner_id=0, service="snowflake", key="user", value="my_user", is_secret=False))
        session.add(Credential(owner_id=0, service="snowflake", key="password", value=foreign_token, is_secret=True))
        session.commit()

    with pytest.raises(CredentialDecryptionError) as exc_info:
        cm.get_credentials("snowflake", mask_secrets=False)

    err = str(exc_info.value)
    assert "re-enter credential 'password'" in err
    assert "snowflake" in err
    # Confirm it is treated as MissingCredentialError and RepositoryError
    assert isinstance(exc_info.value, MissingCredentialError)
    assert isinstance(exc_info.value, RepositoryError)


def test_test_connection_surfaces_reenter_error_and_never_calls_connector(
    foreign_token, monkeypatch
):
    cm = CredentialManager(url_override="sqlite:///:memory:")
    with cm._session() as session:
        session.add(Credential(owner_id=0, service="snowflake", key="account", value="my_account", is_secret=False))
        session.add(Credential(owner_id=0, service="snowflake", key="user", value="my_user", is_secret=False))
        session.add(Credential(owner_id=0, service="snowflake", key="password", value=foreign_token, is_secret=True))
        session.commit()

    # Patch CredentialManager in credential_manager module
    monkeypatch.setattr(
        "semabridge.repository.credential_manager.CredentialManager",
        lambda *args, **kwargs: cm,
    )

    mock_connect = MagicMock()
    with patch("snowflake.connector.connect", mock_connect):
        result = asyncio.run(run_test_connection("snowflake"))

    # Connector must NEVER be called with ciphertext or at all
    mock_connect.assert_not_called()

    # Result must be failure with clear re-enter message
    assert result["status"] == "failed"
    assert "re-enter credential 'password'" in result["message"]
    assert "snowflake" in result["message"]
