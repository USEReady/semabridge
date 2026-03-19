#!/usr/bin/env python
"""
Quick Summary - Check What Was Synced Without Running Full Tests

Just connects to Snowflake and shows:
1. Semantic view exists?
2. How many metrics in the view?
3. Can query a sample metric?
"""

import os
import sys
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def main():
    """Main execution."""
    print("\n" + "="*100)
    print("QUICK SYNC STATUS CHECK")
    print("="*100)
    
    try:
        import snowflake.connector
        from semabridge.core.settings import get_settings
        from dotenv import load_dotenv
        
        load_dotenv()
        settings = get_settings()
        
        print(f"\n🔗 Connecting to Snowflake...")
        conn = snowflake.connector.connect(
            user=settings.snowflake.user,
            password=settings.snowflake.password.get_secret_value(),
            account=settings.snowflake.account,
            warehouse=settings.snowflake.warehouse,
            database=settings.snowflake.database,
            schema=settings.snowflake.schema_name
        )
        
        print(f"✓ Connected to: {settings.snowflake.database}.{settings.snowflake.schema_name}")
        
        cur = conn.cursor()
        
        # Check for semantic view
        print(f"\n📍 Looking for Competitive Marketing semantic view...")
        cur.execute("SHOW SEMANTIC VIEWS LIKE '%COMPETITIVE%'")
        semantic_views = cur.fetchall()
        
        if not semantic_views:
            print("❌ No semantic view found")
            cur.close()
            conn.close()
            return 1
        
        semantic_view_name = semantic_views[0][1]
        print(f"✓ Found: {semantic_view_name}")
        
        # Get DDL and count metrics
        print(f"\n📊 Analyzing metrics...")
        cur.execute(f"SELECT GET_DDL('SEMANTIC VIEW', '{semantic_view_name}')")
        ddl = cur.fetchone()[0]
        
        # Extract metrics
        metrics_start = ddl.find('METRICS (')
        metrics = {}
        
        if metrics_start > -1:
            metrics_end = ddl.find(')', metrics_start)
            metrics_section = ddl[metrics_start+9:metrics_end].strip()
            
            metric_pattern = r'(\w+)\."([^"]+)"\s+AS'
            matches = re.finditer(metric_pattern, metrics_section)
            
            for match in matches:
                metric_name = match.group(2)
                metrics[metric_name] = True
        
        print(f"✓ Found {len(metrics)} metrics")
        
        # Show metric names
        if metrics:
            print(f"\n📋 Metric Names:")
            for i, metric_name in enumerate(sorted(metrics.keys()), 1):
                print(f"  {i:2d}. {metric_name}")
        
        # Test querying a metric
        if metrics:
            sample_metric = sorted(metrics.keys())[0]
            print(f"\n🧪 Testing query on sample metric: {sample_metric}")
            
            try:
                query = f'SELECT {sample_metric} FROM {semantic_view_name}'
                cur.execute(query)
                result = cur.fetchone()
                
                if result:
                    print(f"✓ Query successful - Value: {result[0]}")
                else:
                    print(f"⚠️  Query returned no result")
            
            except Exception as e:
                print(f"❌ Query failed: {e}")
        
        cur.close()
        conn.close()
        
        # Summary
        print("\n" + "="*100)
        print("✅ STATUS SUMMARY")
        print("="*100)
        print(f"\nSemantic View:    {semantic_view_name}")
        print(f"Total Metrics:    {len(metrics)}")
        print(f"Status:           DEPLOYED ✓")
        print("\nTo see full comparison with Fabric measures, run:")
        print("  python compare_fabric_vs_snowflake_measures.py")
        print("\nTo validate all metrics are queryable, run:")
        print("  python validate_synced_metrics.py")
        print("\n")
        
        return 0
    
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
