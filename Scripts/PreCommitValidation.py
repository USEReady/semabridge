"""
Pre-Commit Validation Hook for Semabridge

This script runs before commit to enforce architectural standards.
Can be integrated with git hooks or CI/CD pipeline.
"""

import subprocess
import sys
from pathlib import Path

def run_architecture_validation():
    """Run the architecture validator"""
    print("\n🔐 Running architecture validation...\n")
    
    scripts_path = Path(__file__).parent
    validator_path = scripts_path / "ValidateArchitecture.py"
    
    result = subprocess.run([sys.executable, str(validator_path)], 
                          capture_output=False)
    
    if result.returncode != 0:
        print("\n" + "="*60)
        print("❌ PRE-COMMIT CHECK FAILED")
        print("="*60)
        print("\nYour commit violates Semabridge architectural standards.")
        print("Please fix the violations above and try again.")
        print("\nReference:")
        print("  - .copilot-instructions.md")
        print("  - .github/agents/semabridge-architect.agent.md")
        print("="*60)
        return False
    
    print("\n✅ Architecture validation passed!")
    return True

def check_session_notes():
    """Check if session_notes.md is updated"""
    workspace_root = Path(__file__).parent.parent
    session_notes = workspace_root / "session_notes.md"
    
    if not session_notes.exists():
        print("\n⚠️  No session_notes.md found. Creating...")
        session_notes.write_text("# Session Notes\n\nDocumentation of changes made in this session.\n")
        print("   Please update session_notes.md with your changes.")
        return True  # Don't block on first time
    
    return True

if __name__ == "__main__":
    # Run all checks
    passed_validation = run_architecture_validation()
    passed_session = check_session_notes()
    
    if not passed_validation:
        sys.exit(1)
    
    print("\n✅ All pre-commit checks passed!")
    sys.exit(0)
