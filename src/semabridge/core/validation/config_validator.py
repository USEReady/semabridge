"""
Configuration Validator.

Performs semantic validation on the configuration that goes beyond
structure and schema.
"""

from __future__ import annotations

import os
import re
from typing import List, Dict, Any, Tuple

from semabridge.core.project import ProjectConfig

class ConfigValidator:
    """
    Validates configuration semantics and environment dependencies.
    """
    
    @staticmethod
    def validate_environment(text: str) -> List[Dict[str, Any]]:
        """
        Check if all environment variables referenced in the YAML exist.
        
        Args:
            text: Raw YAML content.
            
        Returns:
            List of error dictionaries.
        """
        errors = []
        
        # Regex to find ${VAR_NAME} pattern
        env_pattern = re.compile(r'\$\{([A-Z0-9_]+)\}')
        
        # Find all matches with line numbers
        lines = text.split('\n')
        for i, line in enumerate(lines):
            matches = env_pattern.findall(line)
            for var_name in matches:
                if var_name not in os.environ:
                    errors.append({
                        "line": i + 1,
                        "message": f"Missing environment variable: {var_name}",
                        "severity": "error"
                    })
                    
        return errors

    @staticmethod
    def validate_logic(config: ProjectConfig) -> List[Dict[str, Any]]:
        """
        validate logical constraints on the parsed configuration.
        
        Args:
            config: Parsed ProjectConfig object.
            
        Returns:
            List of error dictionaries.
        """
        errors = []
        
        # 1. Source Logic
        # (Workspace validation handled by Pydantic model_validator)
        
        # 2. Target Logic
        if not config.targets:
             errors.append({
                "line": 0,
                "message": "At least one target must be defined",
                "severity": "error"
            })
            
        # 3. Model Pattern
        if not config.source.model:
             errors.append({
                "line": 0,
                "message": "Source model pattern cannot be empty (use '*' for all)",
                "severity": "warning"
            })
            
        return errors
