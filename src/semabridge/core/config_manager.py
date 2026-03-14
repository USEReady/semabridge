"""
Configuration Manager.

Centralized state management for semabridge.yaml.
Acts as the single source of truth for the UI and Sync engine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from semabridge.utils.logger import get_logger
from semabridge.core.project import ProjectConfig, load_project_config
from semabridge.core.config_loader import load_yaml_file

# Use ruamel.yaml for round-trip preservation if available, else standard yaml
try:
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
except ImportError:
    import yaml

logger = get_logger(__name__)


class ConfigurationManager(QObject):
    """
    Manages the lifecycle of the semabridge.yaml configuration.
    
    Responsibilities:
    1. Load/Save YAML text
    2. Parse YAML into ProjectConfig (AST)
    3. Run Validation Pipeline
    4. Emit signals on state changes
    """
    
    # Signals
    config_loaded = pyqtSignal(object)       # Emitted when valid config is loaded (ProjectConfig)
    text_changed = pyqtSignal(str)           # Emitted when YAML text changes
    validation_changed = pyqtSignal(bool, list)  # (is_valid, errors)
    saved = pyqtSignal(str)                  # Emitted on save (path)
    
    def __init__(self, config_path: Optional[Path] = None):
        super().__init__()
        from semabridge.core.config_loader import get_default_config_path
        self.config_path = config_path or get_default_config_path() or Path("semabridge.yaml")
        self._current_text: str = ""
        self._current_config: Optional[ProjectConfig] = None
        self._is_valid: bool = False
        self._validation_errors: List[Dict[str, Any]] = []
        
    def load(self) -> bool:
        """Load configuration from disk."""
        try:
            if not self.config_path.exists():
                logger.warning(f"Config file not found: {self.config_path}")
                return False
                
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            self.update_text(content, source="disk")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return False

    def update_text(self, text: str, source: str = "ui"):
        """
        Update the configuration text state.
        
        Args:
            text: New YAML content
            source: Origin of change ('ui', 'disk', 'rollback')
        """
        if text == self._current_text:
            return
            
        self._current_text = text
        
        # Only emit if change didn't come from UI (to avoid loops)
        if source != "ui":
            self.text_changed.emit(text)
            
        # Trigger validation
        self._validate()

    def _validate(self):
        """Run validation pipeline on current text."""
        from semabridge.core.project import ProjectConfig
        from semabridge.core.validation.config_validator import ConfigValidator
        from pydantic import ValidationError
        import yaml as py_yaml # Use standard yaml for safe_load check
        
        errors = []
        self._is_valid = False
        self._current_config = None
        
        # 1. Syntax Validation
        try:
            parsed_data = py_yaml.safe_load(self._current_text)
            if not isinstance(parsed_data, dict):
                errors.append({
                    "line": 1,
                    "message": "Root must be a YAML object (dictionary)",
                    "severity": "error"
                })
                self._emit_validation(False, errors)
                return
        except py_yaml.YAMLError as e:
            # Extract line/col if available
            line = e.problem_mark.line + 1 if hasattr(e, 'problem_mark') else 1
            errors.append({
                "line": line,
                "message": f"Syntax Error: {e.problem}",
                "severity": "error"
            })
            self._emit_validation(False, errors)
            return

        # 2. Environmental Validation (Fast Semantic)
        env_errors = ConfigValidator.validate_environment(self._current_text)
        if env_errors:
            errors.extend(env_errors)
            # We don't stop here, we continue to schema validation if possible

        # 3. Schema Validation (Pydantic)
        try:
            self._current_config = ProjectConfig.from_yaml_dict(parsed_data)
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(l) for l in err['loc'])
                msg = err['msg']
                errors.append({
                    "line": 0, # Pydantic doesn't give lines easily without mapping
                    "message": f"Schema Error [{loc}]: {msg}",
                    "severity": "error"
                })
        
        # If schema failed, we might not have a config object to check logic on
        if self._current_config:
            # 4. Logical Validation
            logic_errors = ConfigValidator.validate_logic(self._current_config)
            errors.extend(logic_errors)
        
        # Determine validity (FAIL if any error severity)
        has_errors = any(e.get("severity", "error") == "error" for e in errors)
        
        self._is_valid = not has_errors
        self._emit_validation(self._is_valid, errors)
        
        if self._is_valid and self._current_config:
            self.config_loaded.emit(self._current_config)

    def _emit_validation(self, is_valid: bool, errors: List[Dict]):
        """Emit validation state change."""
        self._is_valid = is_valid
        self._validation_errors = errors
        self.validation_changed.emit(is_valid, errors)

    def save(self) -> bool:
        """Save current text to disk."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write(self._current_text)
            
            logger.info(f"Saved configuration to {self.config_path}")
            self.saved.emit(str(self.config_path))
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def load_default(self):
        """Load a standard default project template."""
        # Standard default project template provided by User
        template_obj = {
            "source": {
                "type": "fabric",
                "workspace_id": "1a5e9594-c112-43d0-8cdd-012f7746c1b1",
                "model": "Customer Profitability"
            },
            "target": {
                "type": "snowflake",
                "deploy": True
            },
            # model_name defaults to source.model if omitted
            "version_tag": "v1.0",
            "logging": {
                "level": "INFO",
                "format": "text"
            },
            "policy_path": "policies/standard.yaml"
        }
        
        # Format with nice indents
        from ruamel.yaml import YAML
        import io
        ryaml = YAML()
        ryaml.indent(mapping=2, sequence=4, offset=2)
        
        stream = io.StringIO()
        stream.write("# SemaBridge Project Configuration Template\n")
        stream.write("# Required: Source connector configuration\n")
        
        # We manually write the comments to match the requested style
        content = [
            "# Required: Source connector configuration",
            "source:",
            "  type: fabric",
            "  workspace_id: \"1a5e9594-c112-43d0-8cdd-012f7746c1b1\"",
            "  model: \"Customer Profitability\"",
            "  # Optional: dataset_id (defaults to model name if omitted)",
            "  # dataset_id: \"1cb616cc-52b7-4268-b458-b092d64c84a6\"",
            "",
            "# Optional: Target connector configuration",
            "target:",
            "  type: snowflake",
            "  deploy: true  # Set to true to enable deployment",
            "",
            "# Optional: Semantic model name (defaults to source.model if omitted)",
            "# model_name: \"Customer Profitability\"",
            "",
            "# Optional: Snapshot version to sync",
            "version_tag: \"v1.0\"",
            "",
            "# Optional: Sync direction",
            "# sync_direction: source_to_target  # or \"target_to_source\"",
            "",
            "# Optional: Logging configuration",
            "# CLI --log-level takes precedence over this",
            "logging:",
            "  level: INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL",
            "  format: text  # \"text\" or \"json\"",
            "",
            "# Optional: Behavior policy path",
            "policy_path: \"policies/standard.yaml\"",
            ""
        ]
        
        self.update_text("\n".join(content), source="rollback") # rollback source triggers editor update

    def is_valid(self) -> bool:
        return self._is_valid

    def get_config(self) -> Optional[ProjectConfig]:
        return self._current_config
