#!/usr/bin/env python
"""
Validate Synced Metrics - Query and Test Each Metric

This script:
1. Gets all metrics from Snowflake semantic view
2. Tests each metric with actual queries
3. Validates that metrics are queryable and return results
4. Generates a validation report
"""

import os
import sys
import json
import re
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def get_snowflake_metrics():
    """Get all metrics from Snowflake semantic view."""
    print("\n" + "="*100)
    print("CONNECTING TO SNOWFLAKE")
    print("="*100)
    
    try:
        import snowflake.connector
        from semabridge.core.settings import get_settings
        from dotenv import load_dotenv
        
        load_dotenv()
        settings = get_settings()
        
        conn = snowflake.connector.connect(
            user=settings.snowflake.user,
            password=settings.snowflake.password.get_secret_value(),
            account=settings.snowflake.account,
            warehouse=settings.snowflake.warehouse,
            database=settings.snowflake.database,
            schema=settings.snowflake.schema_name
        )
        
        print(f"✓ Connected to Snowflake: {settings.snowflake.database}.{settings.snowflake.schema_name}")
        
        cur = conn.cursor()
        
        # Find semantic view
        cur.execute("SHOW SEMANTIC VIEWS LIKE '%COMPETITIVE%'")
        semantic_views = cur.fetchall()
        
        if not semantic_views:
            print("✗ No semantic view found matching 'COMPETITIVE%'")
            cur.close()
            conn.close()
            return None, None
        
        semantic_view_name = semantic_views[0][1]
        print(f"✓ Found semantic view: {semantic_view_name}")
        
        # Get DDL
        cur.execute(f"SELECT GET_DDL('SEMANTIC VIEW', '{semantic_view_name}')")
        ddl = cur.fetchone()[0]
        
        # Parse metrics from DDL
        metrics = {}
        metrics_start = ddl.find('METRICS (')
        
        if metrics_start > -1:
            metrics_end = ddl.find(')', metrics_start)
            metrics_section = ddl[metrics_start+9:metrics_end].strip()
            
            # Extract metric names: alias."METRIC_NAME"
            metric_pattern = r'(\w+)\."([^"]+)"\s+AS'
            matches = re.finditer(metric_pattern, metrics_section)
            
            for match in matches:
                alias = match.group(1)
                metric_name = match.group(2)
                metrics[metric_name] = alias
        
        print(f"✓ Found {len(metrics)} metrics in semantic view")
        
        return conn, semantic_view_name, metrics
    
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None


def test_metric_queries(conn, semantic_view_name, metrics):
    """Test querying each metric from the semantic view."""
    print("\n" + "="*100)
    print("TESTING METRIC QUERIES")
    print("="*100)
    
    if not metrics:
        print("✗ No metrics to test")
        return None
    
    cur = conn.cursor()
    test_results = {}
    successful = 0
    failed = 0
    
    for metric_name in sorted(metrics.keys()):
        try:
            # Query the metric
            query = f'SELECT {metric_name} FROM {semantic_view_name}'
            print(f"\n📊 Testing: {metric_name}")
            print(f"   Query: {query}")
            
            cur.execute(query)
            result = cur.fetchone()
            
            if result:
                value = result[0]
                print(f"   ✓ SUCCESS - Value: {value}")
                test_results[metric_name] = {
                    'status': 'success',
                    'value': value,
                    'query': query,
                }
                successful += 1
            else:
                print(f"   ⚠️  NO RESULT - Query returned empty")
                test_results[metric_name] = {
                    'status': 'no_result',
                    'value': None,
                    'query': query,
                }
                failed += 1
        
        except Exception as e:
            error_msg = str(e)
            print(f"   ❌ FAILED - {error_msg}")
            test_results[metric_name] = {
                'status': 'failed',
                'error': error_msg,
                'query': query,
            }
            failed += 1
    
    cur.close()
    
    # Summary
    print("\n" + "="*100)
    print("QUERY TEST SUMMARY")
    print("="*100)
    total = len(metrics)
    success_rate = 100 * successful / total if total > 0 else 0
    
    print(f"\nTotal metrics:     {total}")
    print(f"Successful:        {successful} ({success_rate:.1f}%)")
    print(f"Failed:            {failed}")
    
    return test_results


def validate_metric_structure(conn, semantic_view_name):
    """Validate the semantic view structure and metrics."""
    print("\n" + "="*100)
    print("VALIDATING SEMANTIC VIEW STRUCTURE")
    print("="*100)
    
    try:
        cur = conn.cursor()
        
        # Describe semantic view
        print(f"\n📋 Describing semantic view: {semantic_view_name}")
        cur.execute(f"DESCRIBE SEMANTIC VIEW {semantic_view_name}")
        columns = cur.fetchall()
        
        print(f"✓ Semantic view has {len(columns)} columns/metrics:")
        for col in columns:
            col_name = col[0]
            col_type = col[1]
            print(f"  • {col_name:<40} ({col_type})")
        
        cur.close()
        return True
    
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def main():
    """Main execution."""
    print("\n")
    print("╔" + "="*98 + "╗")
    print("║" + " "*30 + "VALIDATE SYNCED METRICS" + " "*46 + "║")
    print("║" + " "*15 + "Query and Test Each Metric from Snowflake" + " "*43 + "║")
    print("╚" + "="*98 + "╝")
    
    # Connect and get metrics
    result = get_snowflake_metrics()
    conn = result[0] if result else None
    semantic_view_name = result[1] if result else None
    metrics = result[2] if result else None
    
    if not conn or not metrics:
        print("\n❌ Failed to connect or find metrics")
        return 1
    
    # Validate structure
    validate_metric_structure(conn, semantic_view_name)
    
    # Test queries
    test_results = test_metric_queries(conn, semantic_view_name, metrics)
    
    # Save results
    os.makedirs('output', exist_ok=True)
    report_file = f"output/metric_query_validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    report = {
        'timestamp': datetime.now().isoformat(),
        'semantic_view': semantic_view_name,
        'total_metrics': len(metrics),
        'test_results': test_results,
    }
    
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n💾 Validation report saved to: {report_file}")
    
    # Final summary
    successful = sum(1 for r in test_results.values() if r['status'] == 'success')
    
    print("\n" + "="*100)
    if successful == len(metrics):
        print("✅ ALL METRICS VALIDATED SUCCESSFULLY")
    else:
        print(f"⚠️  PARTIAL SUCCESS - {successful}/{len(metrics)} metrics working")
    print("="*100 + "\n")
    
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
