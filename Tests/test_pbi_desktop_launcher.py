"""Unit tests for Power BI Desktop External Tools launcher."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
import pytest

from tools.pbi_desktop_launcher.launcher import (
    build_launch_url,
    load_base_url,
    main,
)


def test_build_launch_url():
    base_url = "http://localhost:5180"
    server = "localhost:61470"
    database = "ad023c8e-cfd2-4118-9384-acb93155b216"

    url = build_launch_url(base_url, server, database)
    assert url == "http://localhost:5180/projects/new?source=pbi_desktop&server=localhost%3A61470&database=ad023c8e-cfd2-4118-9384-acb93155b216"


def test_load_base_url_valid(tmp_path):
    cfg_file = tmp_path / "launcher_config.json"
    cfg_file.write_text(json.dumps({"semabridge_base_url": "http://127.0.0.1:5180/"}))

    base = load_base_url(cfg_file)
    assert base == "http://127.0.0.1:5180"


def test_load_base_url_missing_file_raises_error(tmp_path):
    missing_file = tmp_path / "non_existent.json"
    with pytest.raises(RuntimeError, match="Configuration file not found"):
        load_base_url(missing_file)


def test_load_base_url_missing_key_raises_error(tmp_path):
    cfg_file = tmp_path / "launcher_config.json"
    cfg_file.write_text(json.dumps({"other_key": "val"}))

    with pytest.raises(RuntimeError, match="Missing 'semabridge_base_url'"):
        load_base_url(cfg_file)


def test_main_opens_browser(tmp_path, monkeypatch):
    cfg_file = tmp_path / "launcher_config.json"
    cfg_file.write_text(json.dumps({"semabridge_base_url": "https://app.semabridge.internal"}))
    monkeypatch.setenv("SEMABRIDGE_LAUNCHER_CONFIG", str(cfg_file))

    with patch("webbrowser.open") as mock_open:
        exit_code = main(["localhost:61470", "ad023c8e-cfd2-4118-9384-acb93155b216"])
        assert exit_code == 0
        mock_open.assert_called_once_with(
            "https://app.semabridge.internal/projects/new?source=pbi_desktop&server=localhost%3A61470&database=ad023c8e-cfd2-4118-9384-acb93155b216"
        )


def test_main_missing_args(monkeypatch):
    with patch("tools.pbi_desktop_launcher.launcher.show_error") as mock_err:
        exit_code = main(["only_server"])
        assert exit_code == 1
        assert mock_err.called
