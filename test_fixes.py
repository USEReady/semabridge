#!/usr/bin/env python
"""
Quick test to verify that the $ character filtering is applied in snowflake_emitter.py
"""

import sys
from pathlib import Path

# Add src to path
root_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(root_dir / "src"))

def test_metric_filtering():
    """Test that metrics with $ are filtered"""
    print("=" * 70)
    print("TESTING: Metric $ Character Filtering")
    print("=" * 70)
    
    # Read the snowflake_emitter file and check for the fix
    emitter_path = root_dir / "src" / "semabridge" / "connectors" / "snowflake_emitter.py"
    
    with open(emitter_path, 'r') as f:
        content = f.read()
    
    # Check for the filtering fix
    if 'valid_metrics = [m for m in sml.metrics if "$" not in m.unique_name]' in content:
        print("✅ PASS: Metric $ filtering is in place")
        return True
    else:
        print("❌ FAIL: Metric $ filtering NOT found in snowflake_emitter.py")
        return False

def test_relationship_validation():
    """Test that relationship validation guards are in place"""
    print("\n" + "=" * 70)
    print("TESTING: Relationship Validation Guards")
    print("=" * 70)
    
    emitter_path = root_dir / "src" / "semabridge" / "connectors" / "snowflake_emitter.py"
    
    with open(emitter_path, 'r') as f:
        content = f.read()
    
    # Check for the guard condition
    if 'if not from_alias or not to_alias or not rel.from_columns:' in content:
        print("✅ PASS: Relationship validation guards are in place")
        return True
    else:
        print("❌ FAIL: Relationship validation guards NOT found")
        return False

def test_syntax():
    """Test that the modified file has valid Python syntax"""
    print("\n" + "=" * 70)
    print("TESTING: Python Syntax Validation")
    print("=" * 70)
    
    emitter_path = root_dir / "src" / "semabridge" / "connectors" / "snowflake_emitter.py"
    
    try:
        import py_compile
        py_compile.compile(str(emitter_path), doraise=True)
        print("✅ PASS: snowflake_emitter.py has valid Python syntax")
        return True
    except py_compile.PyCompileError as e:
        print(f"❌ FAIL: Syntax error in snowflake_emitter.py:\n{e}")
        return False

if __name__ == "__main__":
    results = []
    results.append(test_metric_filtering())
    results.append(test_relationship_validation())
    results.append(test_syntax())
    
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"Tests Passed: {passed}/{total}")
    
    if all(results):
        print("\n✅ ALL FIXES VERIFIED - Ready for deployment test")
        sys.exit(0)
    else:
        print("\n❌ Some fixes missing or invalid")
        sys.exit(1)
