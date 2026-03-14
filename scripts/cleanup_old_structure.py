"""
Cleanup Script for SemaBridge Restructure.

Removes old duplicate directories and files after restructure is verified.
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).parent

# Directories to remove (old semabridge package at root is now in src/)
DIRS_TO_REMOVE = [
    ROOT / "semabridge",  # Old package directory (now in src/)
    ROOT / "api",         # Root duplicate
    ROOT / "cli",         # Root duplicate  
    ROOT / "config",      # Root duplicate
    ROOT / "emit",        # Root duplicate
    ROOT / "extract",     # Root duplicate
    ROOT / "sml",         # Root duplicate
    ROOT / "state",       # Root duplicate
    ROOT / "transform",   # Root duplicate  
    ROOT / "utils",       # Root duplicate
    ROOT / "enterprise_logging",  # Root duplicate
    # Output directories (can be regenerated)
    ROOT / "output_final",
    ROOT / "output_modular",
    ROOT / "output_modular_v2",
    ROOT / "output_probability",
    ROOT / "test_output",
    ROOT / "test_output_v2",
]

# Files to remove
FILES_TO_REMOVE = [
    ROOT / "update_imports.py",  # Old migration script
    ROOT / "restructure.py",     # Migration script (done)
    ROOT / "update_new_imports.py",  # Migration script (done)
    ROOT / "fix_main_imports.py",  # Migration script (done)
    ROOT / "fix_test_imports.py",  # Migration script (done)
]


def cleanup():
    print("=" * 60)
    print("SemaBridge Cleanup Script")
    print("=" * 60)
    
    # Remove directories
    print("\n1. Removing old directories...")
    for d in DIRS_TO_REMOVE:
        if d.exists():
            try:
                shutil.rmtree(d)
                print(f"  Removed: {d.relative_to(ROOT)}/")
            except Exception as e:
                print(f"  Error removing {d.relative_to(ROOT)}/: {e}")
        else:
            print(f"  Skipped (not found): {d.relative_to(ROOT)}/")
    
    # Remove files
    print("\n2. Removing migration scripts...")
    for f in FILES_TO_REMOVE:
        if f.exists():
            try:
                f.unlink()
                print(f"  Removed: {f.name}")
            except Exception as e:
                print(f"  Error removing {f.name}: {e}")
    
    print("\n" + "=" * 60)
    print("Cleanup complete!")
    print("=" * 60)


if __name__ == "__main__":
    print("\n*** WARNING ***")
    print("This will remove the old semabridge/ directory and duplicates.")
    print("Make sure the restructured project works before running!")
    response = input("\nProceed? (yes/no): ")
    if response.lower() == "yes":
        cleanup()
    else:
        print("Cleanup cancelled.")
