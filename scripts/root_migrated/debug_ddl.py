#!/usr/bin/env python
"""
Debug DDL Generation - Show exact DDL being generated with line numbers
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

def show_ddl_with_lines():
    """Extract and show DDL with line numbers."""
    print("\n" + "="*100)
    print("DDL GENERATION DEBUG")
    print("="*100 + "\n")
    
    try:
        from dotenv import load_dotenv
        from semabridge.core.settings import get_settings
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.converter.tmsl_to_sml import TMSLTransformer
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        
        load_dotenv()
        settings = get_settings()
        
        # Extract from Fabric
        print("[INFO] Extracting from Fabric...")
        extractor = FabricExtractor(config=settings.fabric)
        models = extractor.list_semantic_models()
        
        # Find Competitive Marketing Analysis
        target_model = None
        for model in models:
            if "competitive" in model.get('name', '').lower() and "marketing" in model.get('name', '').lower():
                target_model = model
                break
        
        if not target_model:
            print("[ERROR] Model not found")
            return 1
        
        model_id = target_model['id']
        model_name = target_model['name']
        print(f"[OK] Found: {model_name}")
        
        # Get TMSL
        print("[INFO] Extracting TMSL...")
        tmsl_def = extractor.get_model_definition(model_id)
        tmsl = json.loads(tmsl_def) if isinstance(tmsl_def, str) else tmsl_def
        
        # Convert to SML
        print("[INFO] Converting TMSL to SML...")
        converter = TMSLTransformer()
        sml = converter.transform(tmsl, settings.fabric.workspace_id, model_id)
        
        print(f"[OK] Metrics in SML: {len(sml.metrics)}")
        
        # Generate DDL
        print("[INFO] Generating DDL...")
        emitter = SnowflakeEmitter(
            snowflake_config=settings.snowflake,
            sml_model=sml,
        )
        
        ddl = emitter.emit_semantic_view_ddl(sml)
        
        # Show DDL with line numbers
        print("\n" + "-"*100)
        print("GENERATED DDL WITH LINE NUMBERS")
        print("-"*100 + "\n")
        
        lines = ddl.split('\n')
        
        # Show all lines
        for i, line in enumerate(lines, 1):
            print(f"{i:4d}: {line}")
        
        # Highlight problem lines
        print("\n" + "-"*100)
        print("PROBLEM AREAS (Lines mentioned in Snowflake error)")
        print("-"*100 + "\n")
        
        problem_lines = [79, 82, 125]
        for line_num in problem_lines:
            if line_num <= len(lines):
                line = lines[line_num - 1]
                print(f"Line {line_num}: {line}")
                if len(line) > 32:
                    print(f"  Position 32: ...{line[25:40]}...")
        
        # Check for SELECT in METRICS clause
        print("\n" + "-"*100)
        print("METRICS CLAUSE ANALYSIS")
        print("-"*100 + "\n")
        
        metrics_start = ddl.find('METRICS (')
        if metrics_start > -1:
            metrics_end = ddl.find(')', metrics_start)
            metrics_section = ddl[metrics_start:metrics_end+1]
            
            print("Metrics clause:")
            for i, line in enumerate(metrics_section.split('\n'), 1):
                if 'SELECT' in line.upper():
                    print(f"  [ERROR] Line {i}: {line}")
                else:
                    print(f"  Line {i}: {line}")
        else:
            print("[ERROR] METRICS clause not found!")
        
        # Save DDL to file
        os.makedirs('output', exist_ok=True)
        ddl_file = f"output/debug_ddl_{model_name.replace(' ', '_')}.sql"
        with open(ddl_file, 'w') as f:
            f.write(ddl)
        
        print(f"\n[INFO] Full DDL saved to: {ddl_file}")
        
        print("\n" + "="*100)
        return 0
    
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(show_ddl_with_lines())
