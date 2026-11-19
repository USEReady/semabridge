"""Path allow-list for PBIX import/browse and upload size cap."""

from __future__ import annotations

import asyncio
import io
import os

import pytest

import semabridge.auth.file_access as fa
from semabridge.domain.exceptions import PermissionError as DomainPermissionError
from semabridge.domain.exceptions import ValidationError


@pytest.fixture
def root(tmp_path, monkeypatch):
    allowed = (tmp_path / "allowed").resolve()
    allowed.mkdir()
    monkeypatch.setattr(fa, "allowed_source_roots", lambda: [allowed])
    return allowed


def test_allowed_file_resolves(root):
    f = root / "a.pbix"
    f.write_bytes(b"x")
    assert fa.resolve_allowed_path(str(f), allowed_suffixes={".pbix"}) == f


def test_traversal_rejected(root):
    outside = root.parent / "secret.pbix"
    outside.write_bytes(b"x")
    with pytest.raises(DomainPermissionError):
        fa.resolve_allowed_path(str(root / ".." / "secret.pbix"))


def test_symlink_escape_rejected(root):
    outside = root.parent / "outside.pbix"
    outside.write_bytes(b"x")
    link = root / "link.pbix"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"Symlinks not supported on this host: {exc}")
    with pytest.raises(DomainPermissionError):
        fa.resolve_allowed_path(str(link))


def test_wrong_suffix_rejected(root):
    f = root / "notes.txt"
    f.write_text("x")
    with pytest.raises(ValidationError):
        fa.resolve_allowed_path(str(f), allowed_suffixes={".pbix"})


def test_default_roots_include_upload_root_and_examples(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMABRIDGE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(fa, "_local_folder_roots", lambda: [tmp_path / "lf"])
    monkeypatch.setattr(fa, "_models_path_root", lambda: [])
    roots = fa.allowed_source_roots()
    assert (tmp_path / "uploads").resolve() in roots
    assert (tmp_path / "lf").resolve() in roots
    assert any(r.name == "Examples" for r in roots)


def test_import_pbix_rejects_outside_path(root):
    from semabridge.api.services.project_pbix_impl import import_pbix

    with pytest.raises(DomainPermissionError):
        asyncio.run(import_pbix({"pbix_path": "/etc/hosts"}))


def test_browse_rejects_outside_dir_and_caps_results(root, monkeypatch):
    from semabridge.api.services import project_pbix_impl as impl

    with pytest.raises(DomainPermissionError):
        asyncio.run(impl.browse_pbix_files("/"))

    for i in range(5):
        (root / f"m{i}.pbix").write_bytes(b"x")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "skip.pbix").write_bytes(b"x")
    monkeypatch.setenv("SEMABRIDGE_PBIX_BROWSE_LIMIT", "3")
    out = asyncio.run(impl.browse_pbix_files(str(root)))
    assert len(out["files"]) == 3
    assert out["truncated"] is True

    monkeypatch.setenv("SEMABRIDGE_PBIX_BROWSE_LIMIT", "100")
    out = asyncio.run(impl.browse_pbix_files(str(root)))
    assert len(out["files"]) == 5
    assert all("node_modules" not in f["path"] for f in out["files"])


class _Upload:
    def __init__(self, name, data):
        self.filename = name
        self.file = io.BytesIO(data)


def test_upload_size_cap(tmp_path, monkeypatch):
    from semabridge.api.services import pbix_service

    monkeypatch.setenv("SEMABRIDGE_MAX_UPLOAD_MB", "1")
    with pytest.raises(ValidationError):
        pbix_service._save_uploaded_pbix_file(_Upload("big.pbix", b"x" * (1024 * 1024 + 1)), tmp_path)
    assert list(tmp_path.iterdir()) == []  # partial file removed

    saved = pbix_service._save_uploaded_pbix_file(_Upload("ok.pbix", b"x" * 2048), tmp_path)
    assert saved.read_bytes() == b"x" * 2048

    with pytest.raises(ValidationError):
        pbix_service._save_uploaded_pbix_file(_Upload("empty.pbix", b""), tmp_path)


def test_upload_project_rejects_traversal_project_id(tmp_path, monkeypatch):
    from semabridge.api.services import pbix_service

    with pytest.raises(ValidationError):
        asyncio.run(pbix_service.upload_project_pbix("..", _Upload("a.pbix", b"x")))
