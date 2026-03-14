"""
Unit tests for Semabridge initialization module.

Tests the SemabridgeInitializer class including:
- Repository creation with schema
- Config.yaml generation
- Migration of existing databases
- Local mode behavior
- Error handling
"""

import pytest
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from semabridge.core.initializer import SemabridgeInitializer, InitResult, DEFAULT_CONFIG


class TestSemabridgeInitializer:
    """Tests for SemabridgeInitializer class."""
    
    def test_init_creates_database(self, tmp_path):
        """Test that initialization creates a DuckDB database."""
        repo_path = tmp_path / "test.db"
        
        initializer = SemabridgeInitializer(repository_path=repo_path)
        result = initializer.initialize()
        
        assert result.success is True
        assert repo_path.exists()
        assert result.repository_path == repo_path
    
    def test_init_creates_config(self, tmp_path):
        """Test that initialization creates semabridge.yaml."""
        repo_path = tmp_path / "semabridge.db"
        config_path = tmp_path / "config.yaml"
        
        initializer = SemabridgeInitializer(repository_path=repo_path)
        result = initializer.initialize()
        
        assert result.success is True
        assert config_path.exists()
        assert result.config_path == config_path
    
    def test_init_local_flag(self, tmp_path, monkeypatch):
        """Test that --local flag creates repo in current directory."""
        monkeypatch.chdir(tmp_path)
        
        initializer = SemabridgeInitializer(local=True)
        result = initializer.initialize()
        
        assert result.success is True
        assert (tmp_path / "semabridge.db").exists()
        assert result.repository_path == tmp_path / "semabridge.db"
    
    def test_init_idempotent(self, tmp_path):
        """Test that running init twice doesn't cause errors."""
        repo_path = tmp_path / "test.db"
        
        initializer = SemabridgeInitializer(repository_path=repo_path)
        
        # First init
        result1 = initializer.initialize()
        assert result1.success is True
        assert result1.config_path is not None  # Config created
        
        # Second init
        result2 = initializer.initialize()
        assert result2.success is True
        assert result2.config_path is None  # Config already existed
    
    def test_config_yaml_valid_syntax(self, tmp_path):
        """Test that generated config is valid YAML."""
        repo_path = tmp_path / "test.db"
        
        initializer = SemabridgeInitializer(repository_path=repo_path)
        initializer.initialize()
        
        config_path = tmp_path / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        assert config is not None
        assert "core" in config
        assert "database" in config
        assert "logging" in config
        assert "options" in config
    
    def test_config_has_required_keys(self, tmp_path):
        """Test that generated config has all required keys."""
        repo_path = tmp_path / "test.db"
        
        initializer = SemabridgeInitializer(repository_path=repo_path)
        initializer.initialize()
        
        config_path = tmp_path / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        # Check core settings
        assert config["core"]["intermediate_format"] == "OSI"
        assert config["core"]["backup_retention"] == 5
        assert "repository_path" in config["core"]
        
        # Check logging settings
        assert config["logging"]["level"] == "INFO"
        assert config["logging"]["redact_secrets"] is True

        # Check database settings (default is now orm)
        assert config["database"]["backend"] == "orm"
        assert config["database"]["connection_url_env"] == "SEMABRIDGE_DATABASE_URL"
        
        # Check options
        assert "global_exclude_models" in config["options"]

    def test_init_generates_orm_database_config(self, tmp_path):
        """Test that init writes ORM backend settings when selected."""
        repo_path = tmp_path / "semabridge.db"

        initializer = SemabridgeInitializer(
            repository_path=repo_path,
            db_backend="orm",
            orm_connection_url_env="MY_DATABASE_URL",
        )
        result = initializer.initialize()

        assert result.success is True
        config_path = tmp_path / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        assert config["database"]["backend"] == "orm"
        assert config["database"]["connection_url_env"] == "MY_DATABASE_URL"
        assert "db_path" not in config["database"]
        assert "writer_thread_queue" not in config["database"]

    def test_initializer_rejects_invalid_database_backend(self, tmp_path):
        """Test initializer fails fast on unsupported database backend."""
        repo_path = tmp_path / "semabridge.db"

        with pytest.raises(ValueError):
            SemabridgeInitializer(repository_path=repo_path, db_backend="sqlite")
    
    def test_database_has_required_tables(self, tmp_path):
        """Test that created database has all required tables."""
        import duckdb
        
        repo_path = tmp_path / "test.db"
        
        initializer = SemabridgeInitializer(repository_path=repo_path)
        initializer.initialize()
        
        conn = duckdb.connect(str(repo_path))
        try:
            result = conn.execute("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'main'
            """).fetchall()
            table_names = {r[0] for r in result}
            
            assert "projects" in table_names
            assert "snapshots" in table_names
            assert "changes" in table_names
            assert "runs" in table_names
            assert "source_artifacts" in table_names
            assert "semabridge_meta" in table_names
        finally:
            conn.close()
    
    def test_migration_adds_new_columns(self, tmp_path):
        """Test that migration adds new columns to existing tables."""
        import duckdb
        
        repo_path = tmp_path / "test.db"
        
        # Create old-style database without new columns
        conn = duckdb.connect(str(repo_path))
        conn.execute("""
            CREATE TABLE projects (
                project_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                workspace_id VARCHAR,
                last_updated TIMESTAMP
            );
            CREATE TABLE snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                version_tag VARCHAR,
                sml_blob JSON
            );
        """)
        conn.close()
        
        # Run initializer (should migrate)
        initializer = SemabridgeInitializer(repository_path=repo_path)
        result = initializer.initialize()
        
        assert result.success is True
        assert result.migrated is True
        
        # Verify new columns exist
        conn = duckdb.connect(str(repo_path))
        try:
            # Check projects columns
            cols = {c[1] for c in conn.execute("PRAGMA table_info('projects')").fetchall()}
            assert "adapter" in cols
            assert "source_connection" in cols
            
            # Check snapshots columns
            cols = {c[1] for c in conn.execute("PRAGMA table_info('snapshots')").fetchall()}
            assert "status" in cols
            assert "duration_ms" in cols
            assert "error_message" in cols
            assert "initiated_by" in cols
            assert "run_id" in cols
        finally:
            conn.close()
    
    def test_schema_version_recorded(self, tmp_path):
        """Test that schema version is recorded in semabridge_meta."""
        import duckdb
        
        repo_path = tmp_path / "test.db"
        
        initializer = SemabridgeInitializer(repository_path=repo_path)
        initializer.initialize()
        
        conn = duckdb.connect(str(repo_path))
        try:
            result = conn.execute(
                "SELECT value FROM semabridge_meta WHERE key = 'schema_version'"
            ).fetchone()
            
            assert result is not None
            assert result[0] == "1.2"
        finally:
            conn.close()


class TestInitResult:
    """Tests for InitResult model."""
    
    def test_init_result_success(self, tmp_path):
        """Test InitResult for successful initialization."""
        result = InitResult(
            success=True,
            repository_path=tmp_path / "test.db",
            config_path=tmp_path / "config.yaml",
            message="Success",
        )
        
        assert result.success is True
        assert result.migrated is False  # Default
    
    def test_init_result_failure(self, tmp_path):
        """Test InitResult for failed initialization."""
        result = InitResult(
            success=False,
            repository_path=tmp_path / "test.db",
            message="Permission denied",
        )
        
        assert result.success is False
        assert result.config_path is None


class TestDefaultPaths:
    """Tests for default path resolution."""
    
    def test_default_repository_path(self):
        """Test default repository path is in home directory."""
        path = SemabridgeInitializer.get_default_repository_path()
        
        assert path.parent.name == ".semabridge"
        assert path.name == "semabridge.db"
    
    def test_default_config_path(self):
        """Test default config path is in home directory."""
        path = SemabridgeInitializer.get_default_config_path()
        
        assert path.parent.name == ".semabridge"
        assert path.name == "config.yaml"
