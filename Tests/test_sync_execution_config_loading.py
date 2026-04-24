from __future__ import annotations

import pytest
from fastapi import HTTPException

from semabridge.api.services import sync_execution_service as ses


def test_load_config_without_project_or_content_requires_explicit_config(monkeypatch):
    from semabridge.core import config_loader

    monkeypatch.setattr(config_loader, "get_default_config_path", lambda: "")

    with pytest.raises(HTTPException) as exc_info:
        ses._load_config({}, lambda value: value)

    assert exc_info.value.status_code == 400
    assert "Provide project_id or content" in str(exc_info.value.detail)


def test_load_config_with_missing_project_id_does_not_fallback_to_semabridge(monkeypatch):
    from semabridge.core import config_loader

    monkeypatch.setattr(config_loader, "get_config", lambda _project_id: (_ for _ in ()).throw(FileNotFoundError("missing")))

    with pytest.raises(HTTPException) as exc_info:
        ses._load_config({"project_id": "proj-missing"}, lambda value: value)

    assert exc_info.value.status_code == 404
    assert "Config/projects" in str(exc_info.value.detail)
