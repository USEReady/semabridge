#!/usr/bin/env python
"""
Safe Debugging Tool for Semantic View Generation Issues.
Analyzes Stage 8 generated DDL without modifying core pipeline.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Reconfigure stdout/stderr to use UTF-8 to support emoji characters on Windows terminals
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')


def find_latest_debug_dir() -> Path:
    """Find the most recent debug output directory containing raw_fabric_model.json."""
    debug_root = Path("output/debug")
    if not debug_root.exists():
        print("❌ No debug output found. Run deployment first.")
        sys.exit(1)
    
    # Recursively find all raw_fabric_model.json files
    model_files = list(debug_root.glob("**/raw_fabric_model.json"))
    if not model_files:
        # Fall back to finding any directories as before if none found
        dirs = sorted([d for d in debug_root.iterdir() if d.is_dir()], 
                      key=lambda x: x.stat().st_mtime, reverse=True)
        if not dirs:
            print("❌ No debug directories found.")
            sys.exit(1)
        return dirs[0]
    
    # Sort model files by modification time of the file or their parent directory
    model_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return model_files[0].parent


def extract_fabric_model_ddl(model_path: Path) -> str:
    """Extract DDL from raw_fabric_model.json."""
    try:
        with open(model_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Navigate through JSON structure to find DDL
        if isinstance(data, dict):
            # Check for direct DDL field
            if 'ddl' in data:
                return data['ddl']
            # Check nested structures
            if 'model' in data and 'ddl' in data['model']:
                return data['model']['ddl']
        
        return None
    except Exception as e:
        print(f"⚠️  Error reading JSON: {e}")
        return None

def analyze_ddl_issues(ddl: str) -> Dict:
    """Analyze DDL for known issues."""
    issues = {
        'dollar_signs': [],
        'unquoted_references': [],
        'blank_aliases': [],
        'relationship_errors': [],
        'critical_lines': []
    }
    
    lines = ddl.split('\n')
    
    for idx, line in enumerate(lines, 1):
        # Check for $ characters in identifiers
        if '."' in line and '$"' in line:
            issues['dollar_signs'].append({
                'line': idx,
                'content': line.strip()
            })
        
        # Check for REFERENCES with potential issues
        if 'REFERENCES' in line:
            if '(.)' in line or '( .)' in line or 'REFERENCES (' in line:
                issues['relationship_errors'].append({
                    'line': idx,
                    'content': line.strip()
                })
        
        # Check line 119 specifically (common error location)
        if idx == 119:
            issues['critical_lines'].append({
                'line': idx,
                'content': line.strip()
            })
        
        # Check line 1831 (from error message)
        if idx == 1831:
            issues['critical_lines'].append({
                'line': idx,
                'content': line.strip()
            })
    
    return issues

def validate_schema_compatibility(issues: Dict) -> List[str]:
    """Generate recommendations based on issues found."""
    recommendations = []
    
    if issues['dollar_signs']:
        recommendations.append(
            f"✓ Found {len(issues['dollar_signs'])} columns with '$' character:\n" +
            "\n".join([f"  Line {i['line']}: {i['content'][:100]}" 
                      for i in issues['dollar_signs'][:5]])
        )
        recommendations.append("  → These have been filtered out by recent fix")
    
    if issues['relationship_errors']:
        recommendations.append(
            f"✓ Found {len(issues['relationship_errors'])} potential relationship issues:\n" +
            "\n".join([f"  Line {i['line']}: {i['content'][:100]}" 
                      for i in issues['relationship_errors'][:5]])
        )
        recommendations.append("  → Check: table aliases not blank, FROM/TO columns present")
    
    if issues['critical_lines']:
        recommendations.append(
            f"✓ Critical lines identified:\n" +
            "\n".join([f"  Line {i['line']}: {i['content']}" 
                      for i in issues['critical_lines']])
        )
    
    if not recommendations:
        recommendations.append("✅ No obvious issues detected in DDL structure")
    
    return recommendations

def generate_column_inventory(ddl: str) -> Dict:
    """Extract all column references for schema validation."""
    inventory = {
        'tables': {},
        'dimensions': [],
        'metrics': [],
        'relationships': []
    }
    
    in_tables = False
    in_dimensions = False
    in_metrics = False
    in_relationships = False
    
    for line in ddl.split('\n'):
        line = line.strip()
        
        if line.startswith('tables ('):
            in_tables = True
            continue
        elif line.startswith('dimensions ('):
            in_tables = False
            in_dimensions = True
            continue
        elif line.startswith('metrics ('):
            in_dimensions = False
            in_metrics = True
            continue
        elif line.startswith('relationships ('):
            in_metrics = False
            in_relationships = True
            continue
        elif line == ');':
            in_tables = in_dimensions = in_metrics = in_relationships = False
        
        if in_tables and 'AS' in line:
            inventory['tables'][line.split()[0]] = line
        elif in_dimensions and 'as' in line.lower():
            inventory['dimensions'].append(line[:80])
        elif in_metrics and 'AS' in line:
            inventory['metrics'].append(line[:80])
        elif in_relationships and '(' in line:
            inventory['relationships'].append(line[:80])
    
    return inventory

def main():
    """Main debugging workflow."""
    print("\n" + "="*70)
    print("🔍 SEMANTIC VIEW DEBUG ANALYSIS")
    print("="*70 + "\n")
    
    # Try to find the latest DDL SQL file under output/debug
    debug_root = Path("output/debug")
    if not debug_root.exists():
        print("❌ No debug output found. Run deployment first.")
        sys.exit(1)
        
    sql_files = list(debug_root.glob("**/*.sql"))
    if not sql_files:
        print("❌ No SQL files found under output/debug.")
        sys.exit(1)
        
    sql_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    ddl_file = sql_files[0]
    print(f"📄 Found latest DDL file: {ddl_file.name}")
    print(f"📂 Location: {ddl_file.parent}\n")
    
    try:
        ddl = ddl_file.read_text(encoding="utf-8")
    except Exception as e:
        print(f"❌ Error reading DDL file: {e}")
        sys.exit(1)
    
    ddl_lines = len(ddl.split('\n'))
    print(f"📊 DDL Statistics:")
    print(f"   Total lines: {ddl_lines}")
    print(f"   Total size: {len(ddl):,} bytes\n")

    
    # Analyze issues
    print("🔎 Scanning for known issues...\n")
    issues = analyze_ddl_issues(ddl)
    
    # Generate recommendations
    recommendations = validate_schema_compatibility(issues)
    for rec in recommendations:
        print(rec)
    
    # Generate inventory
    print("\n📦 Generated Artifact Inventory:")
    inventory = generate_column_inventory(ddl)
    print(f"   Tables: {len(inventory['tables'])}")
    print(f"   Dimensions: {len(inventory['dimensions'])}")
    print(f"   Metrics: {len(inventory['metrics'])}")
    print(f"   Relationships: {len(inventory['relationships'])}")
    
    # Save full analysis
    report_file = Path("output/debug_analysis_report.txt")
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write("SEMANTIC VIEW DEBUG ANALYSIS REPORT\n")
        f.write("="*70 + "\n\n")
        f.write(f"Debug Directory: {ddl_file.parent}\n")
        f.write(f"DDL Lines: {ddl_lines}\n\n")
        f.write("ISSUES FOUND:\n")
        f.write("-"*70 + "\n")
        for key, values in issues.items():
            if values:
                f.write(f"\n{key.upper()}: {len(values)} found\n")
                for v in values:
                    f.write(f"  Line {v['line']}: {v['content']}\n")
        f.write("\n\nRECOMMENDATIONS:\n")
        f.write("-"*70 + "\n")
        for rec in recommendations:
            f.write(rec + "\n")
    
    print(f"\n✅ Full report saved to: {report_file}\n")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
