"""TMDL Grammar Validator.

Validates Tabular Model Definition Language (TMDL) syntax before parsing,
ensuring consistent indentation and required keywords.
"""

from typing import List, Tuple
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class TmdlGrammarValidator:
    """Validate TMDL syntax before parsing."""

    def validate(self, tmdl_content: str) -> Tuple[bool, List[str]]:
        """Validate TMDL string.
        
        Args:
            tmdl_content: The raw TMDL content string.
            
        Returns:
            Tuple containing boolean (is_valid) and a list of error messages.
        """
        errors = []
        lines = tmdl_content.splitlines()
        
        # Check basic structure
        has_table = False
        in_expression = False
        
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            # Skip empty lines and comments
            if not stripped or stripped.startswith('//'):
                continue
                
            if stripped.startswith('table '):
                has_table = True
                in_expression = False
                continue
                
            if stripped.startswith('column '):
                if not has_table:
                    errors.append(f"Line {i}: 'column' defined outside of a table block.")
                in_expression = False
                continue
                
            if stripped.startswith("measure '"):
                if not has_table:
                    errors.append(f"Line {i}: 'measure' defined outside of a table block.")
                in_expression = True
                # Check for '=' in measure definition
                if '=' not in stripped:
                    errors.append(f"Line {i}: 'measure' definition missing '=' assignment.")
                continue
                
            # If we aren't in a table, and it's not a top-level keyword like 'model'
            if not has_table and not stripped.startswith(('create ', 'model ')):
                errors.append(f"Line {i}: Invalid syntax outside table block.")
                
        if not has_table and len(lines) > 0:
            errors.append("No 'table' definition found in TMDL content.")
            
        is_valid = len(errors) == 0
        if not is_valid:
            logger.warning(f"TMDL validation failed with {len(errors)} errors.")
            for err in errors:
                logger.debug(f"TMDL Error: {err}")
                
        return is_valid, errors
