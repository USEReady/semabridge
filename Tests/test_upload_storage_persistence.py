"""Uploaded source files must outlive the OS temp dir (purged by macOS), and a
re-uploaded model must keep its semantic view name."""

from pathlib import Path

from semabridge.utils import upload_storage
from semabridge.utils.project_models import assign_model_view_names

_A = "6d2f082dcbd14b428deacaec04528c3e_Sales Model.pbix"


def test_temp_dir_uploads_are_copied_to_persistent_storage(tmp_path, monkeypatch):
    temp_root = tmp_path / "tmp" / "semabridge"
    (temp_root / "uploads").mkdir(parents=True)
    src = temp_root / "uploads" / _A
    src.write_bytes(b"pbix")
    monkeypatch.setattr(upload_storage, "legacy_temp_upload_root", lambda: temp_root)
    monkeypatch.setenv("SEMABRIDGE_UPLOAD_DIR", str(tmp_path / "store"))

    cfg = {"source": {"type": "pbix", "mode": "multiple", "models": [str(src), "/elsewhere/keep.pbix"],
                      "model_views": {str(src): "SALES_MODEL"}}}
    assert upload_storage.persist_source_uploads(cfg) is True

    moved = cfg["source"]["models"][0]
    assert Path(moved).read_bytes() == b"pbix"
    assert Path(moved).parent == (tmp_path / "store" / "files").resolve()
    assert cfg["source"]["models"][1] == "/elsewhere/keep.pbix"
    assert cfg["source"]["model_views"] == {moved: "SALES_MODEL"}
    # Idempotent once nothing is left in the temp dir.
    assert upload_storage.persist_source_uploads(cfg) is False


def test_missing_temp_file_is_left_untouched(tmp_path, monkeypatch):
    temp_root = tmp_path / "semabridge"
    monkeypatch.setattr(upload_storage, "legacy_temp_upload_root", lambda: temp_root)
    cfg = {"source": {"type": "pbix", "models": [str(temp_root / "uploads" / _A)]}}
    assert upload_storage.persist_source_uploads(cfg) is False


def test_reuploaded_model_keeps_its_view_name():
    old = f"/up/{_A}"
    new = "/up/0513cdf9944547c186f497c8e0f68511_Sales Model.pbix"
    views = assign_model_view_names({
        "type": "pbix", "models": [new, "/up/Other.pbix"],
        "model_views": {old: "SALES_CUSTOM", "/up/Other.pbix": "OTHER"},
    })
    assert views == {new: "SALES_CUSTOM", "/up/Other.pbix": "OTHER"}
