"""
Regression test for: FabricExtractor service-principal token uses wrong OAuth scope.

Bug: The service-principal (client_credentials) flow in FabricExtractor._get_access_token()
requested scope 'https://analysis.windows.net/powerbi/api/.default' (Power BI scope).
The Fabric REST API (api.fabric.microsoft.com) rejects Power BI-scoped tokens with 401.

Fix: Use 'https://api.fabric.microsoft.com/.default' — the same scope used by
FabricPublisher and MultiWorkspaceOrchestrator.
"""
from __future__ import annotations

import inspect
import re

import pytest


def test_fabric_extractor_service_principal_uses_fabric_scope():
    """
    The service-principal token request in FabricExtractor must use the Fabric API
    scope, not the Power BI scope.

    This test FAILS without the fix (wrong scope) and PASSES with it (correct scope).
    """
    from semabridge.connectors.fabric_extractor import FabricExtractor

    source = inspect.getsource(FabricExtractor._get_access_token)

    # The wrong scope must NOT appear anywhere in the method.
    assert "analysis.windows.net" not in source, (
        "FabricExtractor._get_access_token still uses the Power BI scope "
        "('analysis.windows.net/powerbi/api/.default'). "
        "This causes 401 Unauthorized when calling api.fabric.microsoft.com. "
        "Use 'https://api.fabric.microsoft.com/.default' instead."
    )

    # The correct Fabric scope MUST be present.
    assert "api.fabric.microsoft.com/.default" in source, (
        "FabricExtractor._get_access_token must request the Fabric API scope "
        "('https://api.fabric.microsoft.com/.default') for service-principal auth."
    )


def test_fabric_extractor_scope_matches_publisher_and_orchestrator():
    """
    All three Fabric connectors must use the same OAuth scope for service-principal
    token acquisition so they can all call api.fabric.microsoft.com successfully.
    """
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.connectors.fabric_publisher import FabricPublisher
    from semabridge.connectors.multi_workspace_orchestrator import MultiWorkspaceOrchestrator

    fabric_scope = "https://api.fabric.microsoft.com/.default"

    for cls in (FabricExtractor, FabricPublisher, MultiWorkspaceOrchestrator):
        source = inspect.getsource(cls)
        assert fabric_scope in source, (
            f"{cls.__name__} does not use the Fabric API scope '{fabric_scope}'. "
            "All Fabric connectors must use this scope for service-principal auth."
        )
