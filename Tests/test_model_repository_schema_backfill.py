import sqlite3

from semabridge.repository.model_repository import ModelRepository


def test_model_repository_backfills_missing_run_sync_mode_column(tmp_path):
    db_path = tmp_path / "legacy_runs.db"

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                final_step INTEGER,
                source_type TEXT,
                target_type TEXT,
                error_message TEXT,
                duration_ms INTEGER,
                run_type TEXT,
                before_src_snapshot_id TEXT,
                restored_from_snapshot_id TEXT,
                before_target_snapshot_ids TEXT,
                after_target_snapshot_ids TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    repo = ModelRepository(url_override=f"sqlite:///{db_path.as_posix()}")
    repo.record_run_start(
        run_id="run-legacy-sync-mode",
        project_id="project-1",
        source_type="fabric",
        target_type="sql",
    )

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "sync_mode" in columns

        row = conn.execute(
            "SELECT run_id, sync_mode FROM runs WHERE run_id = ?",
            ("run-legacy-sync-mode",),
        ).fetchone()
        assert row == ("run-legacy-sync-mode", "copy")
    finally:
        conn.close()