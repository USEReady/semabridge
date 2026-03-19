#!/usr/bin/env python
"""
Compare Fabric Measures vs Snowflake Deployed Metrics

This script extracts:
1. All measures from Fabric Competitive Marketing Analysis model
2. All metrics deployed in Snowflake semantic view
3. Shows side-by-side comparison
"""

import os
import sys
import json
import re
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

def get_fabric_measures():
    """Extract all measures from Fabric Competitive Marketing Analysis."""
    print("\n" + "="*100)
    print("EXTRACTING MEASURES FROM FABRIC")
    print("="*100)
    
    try:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.core.settings import get_settings
        from dotenv import load_dotenv
        
        load_dotenv()
        settings = get_settings()
        
        extractor = FabricExtractor(config=settings.fabric)
        models = extractor.list_semantic_models()
        
        # Find Competitive Marketing Analysis
        target_model = None
        for model in models:
            if "competitive" in model.get('name', '').lower() and "marketing" in model.get('name', '').lower():
                target_model = model
                break
        
        if not target_model:
            print("✗ Could not find Competitive Marketing Analysis model")
            return None
        
        model_id = target_model['id']
        model_name = target_model['name']
        print(f"✓ Found model: {model_name}")
        
        # Extract model definition
        print("📥 Extracting model definition...")
        tmsl_def = extractor.get_model_definition(model_id)
        
        if not tmsl_def:
            print("✗ Failed to extract model")
            return None
        
        # Parse TMSL
        try:
            tmsl = json.loads(tmsl_def) if isinstance(tmsl_def, str) else tmsl_def
            model_data = tmsl.get('model', {})
            tables = model_data.get('tables', [])
            
            fabric_measures = {}
            for table in tables:
                measures = table.get('measures', [])
                for measure in measures:
                    measure_name = measure.get('name')
                    fabric_measures[measure_name] = {
                        'fabric_name': measure_name,
                        'table': table.get('name'),
                        'expression': measure.get('expression', ''),
                        'formatString': measure.get('formatString', ''),
                        'displayFolder': measure.get('displayFolder', ''),
                        'isHidden': measure.get('isHidden', False),
                        'description': measure.get('description', ''),
                    }
            
            print(f"✓ Extracted {len(fabric_measures)} measures from Fabric")
            return fabric_measures
        
        except Exception as e:
            print(f"✗ Error parsing TMSL: {e}")
            return None
    
    except Exception as e:
        print(f"✗ Error extracting from Fabric: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_snowflake_metrics():
    """Extract all metrics from Snowflake semantic view."""
    print("\n" + "="*100)
    print("EXTRACTING METRICS FROM SNOWFLAKE")
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
        
        # Check for semantic view
        cur.execute("SHOW SEMANTIC VIEWS LIKE '%COMPETITIVE%'")
        semantic_views = cur.fetchall()
        
        if not semantic_views:
            print("✗ No semantic view found")
            cur.close()
            conn.close()
            return None
        
        semantic_view_name = semantic_views[0][1]
        print(f"✓ Found semantic view: {semantic_view_name}")
        
        # Get DDL to extract metrics
        print("📊 Extracting metrics from semantic view DDL...")
        cur.execute(f"SELECT GET_DDL('SEMANTIC VIEW', '{semantic_view_name}')")
        ddl = cur.fetchone()[0]
        
        # Parse METRICS section from DDL
        snowflake_metrics = {}
        metrics_start = ddl.find('METRICS (')
        
        if metrics_start > -1:
            metrics_end = ddl.find(')', metrics_start)
            metrics_section = ddl[metrics_start+9:metrics_end].strip()
            
            # Parse individual metric definitions
            # Pattern: alias."METRIC_NAME" AS expression
            metric_pattern = r'(\w+)\."([^"]+)"\s+AS\s+(.+?)(?=,\s*\w+\.|$)'
            
            matches = re.finditer(metric_pattern, metrics_section, re.DOTALL)
            for match in matches:
                alias = match.group(1)
                metric_name = match.group(2)
                expression = match.group(3).strip().rstrip(',').strip()
                
                snowflake_metrics[metric_name] = {
                    'snowflake_name': metric_name,
                    'alias': alias,
                    'sql_expression': expression,
                }
        
        print(f"✓ Extracted {len(snowflake_metrics)} metrics from Snowflake")
        
        cur.close()
        conn.close()
        
        return snowflake_metrics
    
    except Exception as e:
        print(f"✗ Error extracting from Snowflake: {e}")
        import traceback
        traceback.print_exc()
        return None


def compare_measures(fabric_measures, snowflake_metrics):
    """Compare Fabric measures with Snowflake metrics."""
    print("\n" + "="*100)
    print("COMPARISON: FABRIC MEASURES vs SNOWFLAKE METRICS")
    print("="*100)
    
    if not fabric_measures:
        print("✗ No Fabric measures to compare")
        return
    
    if not snowflake_metrics:
        print("✗ No Snowflake metrics to compare")
        return
    
    # Normalize names for comparison (remove spaces, special chars)
    def normalize_name(name):
        return re.sub(r'[^A-Za-z0-9]', '', name).upper()
    
    fabric_normalized = {normalize_name(k): k for k in fabric_measures.keys()}
    snowflake_normalized = {normalize_name(k): k for k in snowflake_metrics.keys()}
    
    # Find synced, missing, and extra metrics
    synced = []
    missing = []
    extra = []
    
    for norm_name, orig_name in fabric_normalized.items():
        if norm_name in snowflake_normalized:
            synced.append((orig_name, snowflake_normalized[norm_name]))
        else:
            missing.append(orig_name)
    
    for norm_name, orig_name in snowflake_normalized.items():
        if norm_name not in fabric_normalized:
            extra.append(orig_name)
    
    # Print summary
    total_fabric = len(fabric_measures)
    sync_rate = 100 * len(synced) / total_fabric if total_fabric > 0 else 0
    
    print(f"\n📊 SUMMARY")
    print(f"{'─'*100}")
    print(f"Total Fabric measures:        {total_fabric}")
    print(f"Synced to Snowflake:          {len(synced):3d} ({sync_rate:5.1f}%)")
    print(f"Missing in Snowflake:         {len(missing):3d} ({100*(len(missing)/total_fabric):5.1f}%)")
    print(f"Extra in Snowflake:           {len(extra):3d}")
    print(f"{'─'*100}")
    
    # Synced measures
    if synced:
        print(f"\n✅ SYNCED MEASURES ({len(synced)})")
        print(f"{'─'*100}")
        print(f"{'Fabric Measure':<50} | {'Snowflake Metric':<30} | {'Table':<20}")
        print(f"{'─'*100}")
        for fabric_name, snowflake_name in sorted(synced):
            table = fabric_measures[fabric_name]['table']
            print(f"{fabric_name:<50} | {snowflake_name:<30} | {table:<20}")
    
    # Missing measures
    if missing:
        print(f"\n❌ MISSING IN SNOWFLAKE ({len(missing)})")
        print(f"{'─'*100}")
        print(f"{'Fabric Measure':<50} | {'Table':<30} | {'Status':<20}")
        print(f"{'─'*100}")
        for measure_name in sorted(missing):
            table = fabric_measures[measure_name]['table']
            is_hidden = fabric_measures[measure_name]['isHidden']
            status = "Hidden" if is_hidden else "Not Synced"
            print(f"{measure_name:<50} | {table:<30} | {status:<20}")
    
    # Extra metrics
    if extra:
        print(f"\n⚠️  EXTRA IN SNOWFLAKE ({len(extra)})")
        print(f"{'─'*100}")
        for metric_name in sorted(extra):
            print(f"  • {metric_name}")
    
    # Save detailed comparison
    comparison_report = {
        'timestamp': datetime.now().isoformat(),
        'summary': {
            'total_fabric_measures': total_fabric,
            'synced_measures': len(synced),
            'missing_measures': len(missing),
            'extra_metrics': len(extra),
            'sync_rate_percent': sync_rate,
        },
        'synced': [
            {
                'fabric_name': fabric_name,
                'snowflake_name': snowflake_name,
                'fabric_details': fabric_measures[fabric_name],
                'snowflake_details': snowflake_metrics[snowflake_name],
            }
            for fabric_name, snowflake_name in synced
        ],
        'missing': [
            {
                'fabric_name': measure_name,
                'fabric_details': fabric_measures[measure_name],
            }
            for measure_name in missing
        ],
        'extra': [
            {
                'snowflake_name': metric_name,
                'snowflake_details': snowflake_metrics[metric_name],
            }
            for metric_name in extra
        ],
    }
    
    # Save report
    os.makedirs('output', exist_ok=True)
    report_file = f"output/sync_comparison_competitive_marketing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    with open(report_file, 'w') as f:
        json.dump(comparison_report, f, indent=2, default=str)
    
    print(f"\n💾 Detailed comparison saved to: {report_file}")
    
    return comparison_report


def main():
    """Main execution."""
    print("\n")
    print("╔" + "="*98 + "╗")
    print("║" + " "*25 + "FABRIC vs SNOWFLAKE MEASURE COMPARISON" + " "*36 + "║")
    print("║" + " "*20 + "Competitive Marketing Analysis Model" + " "*42 + "║")
    print("╚" + "="*98 + "╝")
    
    # Step 1: Get Fabric measures
    fabric_measures = get_fabric_measures()
    if not fabric_measures:
        print("\n❌ Failed to extract Fabric measures")
        return 1
    
    # Step 2: Get Snowflake metrics
    snowflake_metrics = get_snowflake_metrics()
    if snowflake_metrics is None:
        print("\n❌ Failed to extract Snowflake metrics")
        return 1
    
    # Step 3: Compare
    comparison_report = compare_measures(fabric_measures, snowflake_metrics)
    
    print("\n" + "="*100)
    print("✅ COMPARISON COMPLETE")
    print("="*100)
    print("\nNext Steps:")
    print("  • Review the comparison report")
    print("  • Check why measures are missing (DAX translation failures?)")
    print("  • Verify metrics in Snowflake using: SELECT * FROM COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC")
    print("\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
