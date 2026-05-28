import aiosmtplib
import pytest
from unittest.mock import AsyncMock, patch
from semabridge.notifications.adapters.email_adapter import EmailAdapter
from semabridge.notifications.utils.validators import validate_smtp_config


# ── Helper to call the static TLS method ──────────────────────────────────────
def _tls_params_for(port, tls_enabled):
    return EmailAdapter._tls_params(port, tls_enabled)


# ── Validator tests ────────────────────────────────────────────────────────────

def test_validate_smtp_config_new_keys():
    """SMTP validator succeeds with all new schema keys."""
    config = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "alerts@example.com",
        "smtp_password": "secretpassword",
        "from_email": "alerts@example.com",
        "to_emails": "recipient1@example.com,recipient2@example.com",
    }
    is_valid, err = validate_smtp_config(config)
    assert is_valid is True
    assert err == ""


def test_validate_smtp_config_legacy_mapping():
    """SMTP validator auto-maps legacy keys and succeeds."""
    config = {
        "host": "smtp.example.com",
        "port": "587",
        "username": "alerts@example.com",
        "password": "secretpassword",
        "from_address": "alerts@example.com",
        "to_addresses": ["recipient1@example.com", "recipient2@example.com"],
    }
    is_valid, err = validate_smtp_config(config)
    assert is_valid is True
    assert err == ""
    assert config["smtp_host"] == "smtp.example.com"
    assert config["smtp_port"] == "587"
    assert config["smtp_username"] == "alerts@example.com"
    assert config["smtp_password"] == "secretpassword"
    assert config["from_email"] == "alerts@example.com"
    assert config["to_emails"] == "recipient1@example.com,recipient2@example.com"


def test_validate_smtp_config_failures():
    """Validator catches missing fields, bad ports, invalid emails."""
    # Missing to_emails
    config = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "alerts@example.com",
        "smtp_password": "password",
        "from_email": "alerts@example.com",
    }
    is_valid, err = validate_smtp_config(config)
    assert is_valid is False
    assert "Missing required SMTP field: to_emails" in err

    # Invalid port range
    config["to_emails"] = "recipient@example.com"
    config["smtp_port"] = 999999
    is_valid, err = validate_smtp_config(config)
    assert is_valid is False
    assert "SMTP port must be between 1 and 65535" in err

    # Invalid from_email format
    config["smtp_port"] = 587
    config["from_email"] = "not-an-email"
    is_valid, err = validate_smtp_config(config)
    assert is_valid is False
    assert "Invalid SMTP from_email" in err

    # Invalid to_emails format
    config["from_email"] = "alerts@example.com"
    config["to_emails"] = "recipient@example.com, invalidemail@"
    is_valid, err = validate_smtp_config(config)
    assert is_valid is False
    assert "Invalid SMTP to_emails recipient" in err


# ── TLS param tests ────────────────────────────────────────────────────────────

def test_tls_params_port_465_is_implicit_ssl():
    """Port 465 should use implicit SSL (use_tls=True, start_tls=False)."""
    use_tls, start_tls = _tls_params_for(465, True)
    assert use_tls is True
    assert start_tls is False


def test_tls_params_port_587_is_starttls():
    """Port 587 with tls_enabled should use STARTTLS (use_tls=False, start_tls=True)."""
    use_tls, start_tls = _tls_params_for(587, True)
    assert use_tls is False
    assert start_tls is True


def test_tls_params_no_tls():
    """tls_enabled=False on non-465 port should be plain SMTP."""
    use_tls, start_tls = _tls_params_for(587, False)
    assert use_tls is False
    assert start_tls is False


# ── Adapter send tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_email_adapter_send_success():
    """EmailAdapter.send succeeds and returns correct response."""
    adapter = EmailAdapter({})

    config = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_username": "alerts@gmail.com",
        "smtp_password": "app-password",
        "from_email": "alerts@gmail.com",
        "to_emails": "admin@example.com, developer@example.com",
        "tls_enabled": True,
    }

    payload = {
        "subject": "Sync Status Success",
        "plaintext": "Synchronized successfully.",
        "html": "<h3>Synchronized successfully.</h3>",
    }

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        result = await adapter.send(payload, config)

    assert result["success"] is True
    assert result["response_code"] == 250

    # Verify aiosmtplib.send was called with correct TLS params
    _, kwargs = mock_send.call_args
    assert kwargs["hostname"] == "smtp.gmail.com"
    assert kwargs["port"] == 587
    assert kwargs["use_tls"] is False    # STARTTLS path, not implicit SSL
    assert kwargs["start_tls"] is True   # STARTTLS enabled
    assert kwargs["username"] == "alerts@gmail.com"
    assert kwargs["password"] == "app-password"


@pytest.mark.asyncio
async def test_email_adapter_send_implicit_ssl():
    """Port 465 uses implicit SSL — start_tls must be False."""
    adapter = EmailAdapter({})

    config = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_username": "alerts@gmail.com",
        "smtp_password": "app-password",
        "from_email": "alerts@gmail.com",
        "to_emails": "admin@example.com",
    }

    payload = {"subject": "Test", "plaintext": "Ok", "html": "<p>Ok</p>"}

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await adapter.send(payload, config)

    _, kwargs = mock_send.call_args
    assert kwargs["use_tls"] is True    # implicit SSL
    assert kwargs["start_tls"] is False  # NOT STARTTLS


@pytest.mark.asyncio
async def test_email_adapter_send_auth_error():
    """SMTPAuthenticationError returns human-readable error without traceback in result."""
    adapter = EmailAdapter({})

    config = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_username": "bad@gmail.com",
        "smtp_password": "wrong-password",
        "from_email": "bad@gmail.com",
        "to_emails": "admin@example.com",
        "tls_enabled": True,
    }

    payload = {"subject": "T", "plaintext": "T", "html": "<p>T</p>"}

    with patch("aiosmtplib.send", side_effect=aiosmtplib.SMTPAuthenticationError(535, "Bad credentials")):
        result = await adapter.send(payload, config)

    assert result["success"] is False
    assert "authentication failed" in result["error"].lower()


@pytest.mark.asyncio
async def test_email_adapter_test_connection():
    """test_connection sends a real test email and returns (True, '')."""
    adapter = EmailAdapter({})

    config = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_username": "alerts@gmail.com",
        "smtp_password": "app-password",
        "from_email": "alerts@gmail.com",
        "to_emails": "admin@example.com",
    }

    with patch("aiosmtplib.send", new_callable=AsyncMock):
        success, err = await adapter.test_connection(config)

    assert success is True
    assert err == ""

