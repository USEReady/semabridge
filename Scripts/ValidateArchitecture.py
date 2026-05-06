"""
Architecture Validation Script for Semabridge APM

This script validates that the codebase follows all architectural standards
defined in .copilot-instructions.md and semabridge-architect.agent.md

Run this as part of pre-commit or CI/CD pipeline.
"""

import os
import re
from pathlib import Path

class ArchitectureValidator:
    """Validates repository against Semabridge architectural standards"""
    
    def __init__(self):
        self.violations = []
        self.workspace_root = Path(__file__).parent.parent
        
    def validate_naming_conventions(self):
        """Check file and folder naming conventions"""
        print("🔍 Validating naming conventions...")
        
        # Check folder names (must be lowercase)
        for item in self.workspace_root.iterdir():
            if item.is_dir() and item.name.startswith('.'):
                continue  # Skip hidden folders
            if item.is_dir() and not item.name.islower():
                if item.name not in ['MyStandards', 'Scratch', 'Config', 'Docs', 'Examples', 'Scripts', 'Tests']:
                    self.violations.append(f"❌ Folder '{item.name}' must be lowercase (found: {item.name})")
        
        # Check file names in src (must be PascalCase)
        src_path = self.workspace_root / "src" / "semabridge"
        if src_path.exists():
            for py_file in src_path.rglob("*.py"):
                if py_file.name.startswith("_"):
                    continue  # Skip dunder files
                if not self._is_pascal_case(py_file.name.replace(".py", "")):
                    self.violations.append(f"❌ File '{py_file.relative_to(self.workspace_root)}' must be PascalCase")
    
    def validate_secrets(self):
        """Check for hardcoded secrets in source code"""
        print("🔍 Validating secrets management...")
        
        secret_patterns = [
            r'api[_-]?key\s*=\s*["\'][^"\']+["\']',
            r'password\s*=\s*["\'][^"\']+["\']',
            r'token\s*=\s*["\'][^"\']+["\']',
            r'secret\s*=\s*["\'][^"\']+["\']',
            r'aws_secret',
            r'private[_-]?key',
        ]
        
        for py_file in self.workspace_root.rglob("*.py"):
            if "venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            try:
                with open(py_file, 'r') as f:
                    content = f.read()
                    for pattern in secret_patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            self.violations.append(f"🚨 SECURITY: Potential secret in {py_file.relative_to(self.workspace_root)}")
            except:
                pass
    
    def validate_folder_structure(self):
        """Check required folder structure"""
        print("🔍 Validating folder structure...")
        
        required_folders = ["src", "tests", "docs", "examples", "scripts"]
        for folder in required_folders:
            if not (self.workspace_root / folder).exists():
                self.violations.append(f"❌ Missing required folder: {folder}/")
    
    def validate_root_hygiene(self):
        """Check root directory doesn't have stray files"""
        print("🔍 Validating root directory hygiene...")
        
        allowed_root_files = {
            "README.md", "LICENSE", "Makefile", "pytest.ini", 
            "pyproject.toml", ".gitignore", "apm.yml", "apm.lock.yaml",
            "requirements.txt", "package.json", "dev.ps1", ".env.example",
            "export_test_data.py", ".python-version"
        }
        
        forbidden_patterns = [
            r"^test_.*\.py$",
            r"^debug_.*\.py$",
            r"^temp_.*",
            r".*\.tmp$",
        ]
        
        for item in self.workspace_root.iterdir():
            if item.is_file():
                if item.name not in allowed_root_files:
                    # Check if it matches forbidden patterns
                    if any(re.match(pattern, item.name) for pattern in forbidden_patterns):
                        self.violations.append(f"❌ Root file '{item.name}' should be in Scripts/ or Tests/")
    
    @staticmethod
    def _is_pascal_case(name):
        """Check if string is PascalCase"""
        return name and name[0].isupper() and "_" not in name and "-" not in name
    
    def report(self):
        """Print validation report"""
        print("\n" + "="*60)
        print("ARCHITECTURE VALIDATION REPORT")
        print("="*60)
        
        if not self.violations:
            print("✅ All architectural standards validated successfully!")
            return 0
        else:
            print(f"\n❌ Found {len(self.violations)} violation(s):\n")
            for violation in self.violations:
                print(f"  {violation}")
            print("\n" + "="*60)
            return 1

if __name__ == "__main__":
    validator = ArchitectureValidator()
    validator.validate_naming_conventions()
    validator.validate_secrets()
    validator.validate_folder_structure()
    validator.validate_root_hygiene()
    exit(validator.report())
