#!/usr/bin/env python
"""
Complete Sync Validation - Master Script

Runs the full validation pipeline:
1. Extracts Fabric measures
2. Extracts Snowflake metrics
3. Compares them
4. Tests query capability on each metric
5. Generates comprehensive report
"""

import os
import sys
import subprocess
import json
from datetime import datetime

def run_comparison():
    """Run the comparison script."""
    print("\n" + "🔵 "*50)
    print("STEP 1: COMPARING FABRIC vs SNOWFLAKE")
    print("🔵 "*50)
    
    result = subprocess.run(
        [sys.executable, "compare_fabric_vs_snowflake_measures.py"],
        cwd=os.path.dirname(__file__)
    )
    
    return result.returncode == 0


def run_validation():
    """Run the validation script."""
    print("\n" + "🟢 "*50)
    print("STEP 2: VALIDATING SYNCED METRICS")
    print("🟢 "*50)
    
    result = subprocess.run(
        [sys.executable, "validate_synced_metrics.py"],
        cwd=os.path.dirname(__file__)
    )
    
    return result.returncode == 0


def generate_executive_summary():
    """Generate an executive summary of the validation."""
    print("\n" + "="*100)
    print("EXECUTIVE SUMMARY")
    print("="*100)
    
    # Find latest comparison report
    output_dir = "output"
    if not os.path.exists(output_dir):
        print("No output reports found")
        return
    
    files = [f for f in os.listdir(output_dir) if f.startswith("sync_comparison_")]
    if files:
        latest_comparison = sorted(files)[-1]
        comparison_path = os.path.join(output_dir, latest_comparison)
        
        try:
            with open(comparison_path, 'r') as f:
                comparison_data = json.load(f)
            
            summary = comparison_data.get('summary', {})
            
            print(f"\n📊 Sync Statistics:")
            print(f"  • Total Fabric Measures:     {summary.get('total_fabric_measures', 0)}")
            print(f"  • Synced to Snowflake:       {summary.get('synced_measures', 0)}")
            print(f"  • Missing in Snowflake:      {summary.get('missing_measures', 0)}")
            print(f"  • Sync Rate:                 {summary.get('sync_rate_percent', 0):.1f}%")
        
        except Exception as e:
            print(f"Could not read comparison report: {e}")
    
    # Find latest validation report
    files = [f for f in os.listdir(output_dir) if f.startswith("metric_query_validation_")]
    if files:
        latest_validation = sorted(files)[-1]
        validation_path = os.path.join(output_dir, latest_validation)
        
        try:
            with open(validation_path, 'r') as f:
                validation_data = json.load(f)
            
            test_results = validation_data.get('test_results', {})
            successful = sum(1 for r in test_results.values() if r.get('status') == 'success')
            total = len(test_results)
            
            print(f"\n✅ Query Validation:")
            print(f"  • Total Metrics Tested:      {total}")
            print(f"  • Successful Queries:        {successful}")
            print(f"  • Success Rate:              {100*successful/total if total > 0 else 0:.1f}%")
        
        except Exception as e:
            print(f"Could not read validation report: {e}")


def main():
    """Main execution."""
    print("\n")
    print("╔" + "="*98 + "╗")
    print("║" + " "*25 + "COMPLETE SYNC VALIDATION PIPELINE" + " "*42 + "║")
    print("║" + " "*20 + "Fabric → Snowflake Measure Sync Verification" + " "*35 + "║")
    print("╚" + "="*98 + "╝")
    
    print(f"\n⏱️  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run comparison
    comparison_ok = run_comparison()
    if not comparison_ok:
        print("\n❌ Comparison script failed")
        return 1
    
    # Run validation
    validation_ok = run_validation()
    if not validation_ok:
        print("\n❌ Validation script failed")
        return 1
    
    # Generate summary
    generate_executive_summary()
    
    print(f"\n⏱️  Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    print("\n" + "="*100)
    print("✅ VALIDATION PIPELINE COMPLETE")
    print("="*100)
    print("\nReports saved to:")
    print("  • output/sync_comparison_competitive_marketing_*.json")
    print("  • output/metric_query_validation_*.json")
    print("\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
