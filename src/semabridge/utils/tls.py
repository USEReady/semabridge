"""TLS verification for outbound HTTP calls (OAuth token endpoints).

Certificates are checked by default. Behind a TLS-intercepting corporate
proxy, point SEMABRIDGE_TLS_CA_BUNDLE at the proxy's CA certificate (the
safe fix). SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY=true turns checking off
entirely; it is insecure and logged every time it is used.
"""
from __future__ import annotations

import logging
import os
from typing import Union

logger = logging.getLogger(__name__)

_TRUE = ("1", "true", "yes", "on")


def requests_verify(purpose: str = "outbound request") -> Union[bool, str]:
    """Value for the ``verify=`` argument of ``requests`` calls."""
    bundle = os.environ.get("SEMABRIDGE_TLS_CA_BUNDLE", "").strip()
    if bundle:
        if os.path.isfile(bundle):
            return bundle
        logger.warning("SEMABRIDGE_TLS_CA_BUNDLE=%s does not exist; using the default CA store", bundle)
    if os.environ.get("SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY", "").strip().lower() in _TRUE:
        logger.warning(
            "TLS certificate verification is DISABLED for %s (SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY). "
            "Set SEMABRIDGE_TLS_CA_BUNDLE to your proxy's CA instead.", purpose,
        )
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception as exc:  # noqa: BLE001 -- only silences a warning
            logger.debug("Could not silence urllib3 warning: %s", exc)
        return False
    return True
