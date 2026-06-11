"""
Test Script: Capture All Intermediate Formats
=============================================

Saves every intermediate artifact produced during the SemaBridge pipeline
to the `output/intermediate_artifacts/` folder with proper naming.

Pipeline stages captured:
  1. raw_tmsl.json          — Raw TMSL extracted from Fabric (Step 4)
  2. source_format.json     — Parsed SourceFormat artifact (Step 5)
  3. osi_model.json         — OSI intermediate model (Step 6 Phase 1)
  4. osi_model.yaml         — OSI intermediate model (YAML)
  5. sml_model.json         — Canonical SML model (Step 6 Phase 2)
  6. sml_model.yaml         — Canonical SML model (YAML)
  7. sml_to_osi_inferred.json — Reverse SML→OSI (post-deployment diagnostic)
  8. snowflake_ddl.sql      — Final DDL sent to Snowflake (Step 8/9)

Usage:
  python tests/test_capture_intermediates.py
"""

import json
import sys
import os
import time
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel
from semabridge.csm.models import CSMModel
from semabridge.converter.adapters.osi_to_csm import OSIToCSMConverter


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "intermediate_artifacts"


def ensure_output_dir():
    """Create output directory structure."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[OK] Output directory: {OUTPUT_DIR}")


def save_json(data: dict, filename: str, stage: str):
    """Save dict as pretty-printed JSON."""
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  [{stage}] Saved: {path.name} ({path.stat().st_size:,} bytes)")


def save_yaml(data: dict, filename: str, stage: str):
    """Save dict as YAML."""
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    print(f"  [{stage}] Saved: {path.name} ({path.stat().st_size:,} bytes)")


def save_text(text: str, filename: str, stage: str):
    """Save raw text."""
    path = OUTPUT_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  [{stage}] Saved: {path.name} ({path.stat().st_size:,} bytes)")


def capture_from_existing_output():
    """
    Capture intermediate formats from the existing output/debug folder
    that the engine already writes to during a real sync.
    """
    print("\n" + "=" * 70)
    print("  CAPTURING INTERMEDIATE FORMATS FROM LAST SYNC RUN")
    print("=" * 70)

    project_root = Path(__file__).resolve().parent.parent
    debug_dir = project_root / "output" / "debug"

    if not debug_dir.exists():
        print(f"\n[WARN] No output/debug directory found at {debug_dir}")
        print("       Run a sync first: semabridge sync --source fabric --target snowflake")
        return False

    # Find the most recent model folder
    model_dirs = [d for d in debug_dir.iterdir() if d.is_dir()]
    if not model_dirs:
        print("[WARN] No model debug folders found")
        return False

    # Use the most recently modified folder
    model_dir = max(model_dirs, key=lambda d: d.stat().st_mtime)
    print(f"\n[INFO] Using model folder: {model_dir.name}")

    ensure_output_dir()
    found_any = False

    # ── Stage 1: Raw TMSL (Step 4 output) ─────────────────────────────
    print("\n--- Stage 1: Raw TMSL (Extraction → before OSI) ---")
    raw_tmsl_path = model_dir / "raw_fabric_model.json"
    if raw_tmsl_path.exists():
        with open(raw_tmsl_path, "r", encoding="utf-8") as f:
            raw_tmsl = json.load(f)
        save_json(raw_tmsl, "1_raw_tmsl.json", "EXTRACTION")
        found_any = True

        # Also extract just the model definition for clarity
        model_def = raw_tmsl.get("model", {})
        tables = model_def.get("tables", [])
        relationships = model_def.get("relationships", [])
        print(f"       Tables: {len(tables)}, Relationships: {len(relationships)}")
    else:
        print(f"  [SKIP] raw_fabric_model.json not found")

    # ── Stage 2: SourceFormat (Step 5 output) ─────────────────────────
    print("\n--- Stage 2: SourceFormat (Parsed extraction artifact) ---")
    # SourceFormat is persisted as a source_artifact in the DB.
    # We reconstruct it from the raw TMSL for demonstration
    if raw_tmsl_path.exists():
        source_format_dict = {
            "format_version": "1.0",
            "source_type": "fabric",
            "extraction_timestamp": str(time.time()),
            "note": "Reconstructed from raw_fabric_model.json for artifact capture",
            "tmsl_tables_count": len(raw_tmsl.get("model", {}).get("tables", [])),
            "tmsl_relationships_count": len(raw_tmsl.get("model", {}).get("relationships", [])),
            "tmsl_expressions_count": len(raw_tmsl.get("model", {}).get("expressions", [])),
        }
        save_json(source_format_dict, "2_source_format_summary.json", "SOURCE_FORMAT")
        found_any = True

    # ── Stage 3: OSI Model (Step 6 Phase 1 output) ────────────────────
    print("\n--- Stage 3: OSI Model (TMSL → OSI conversion) ---")
    # Try to reconstruct OSI from TMSL using the actual converter
    if raw_tmsl_path.exists():
        try:
            from semabridge.converter.tmsl_to_sml import TMSLTransformer
            from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter

            transformer = TMSLTransformer()
            # TMSLTransformer transforms TMSL to intermediate representation? Wait, let's just use TMSLToOSIConverter
            # Let me check the structure first. Actually, I'll just run TMSLToOSIConverter directly
            
            converter = TMSLToOSIConverter()
            source_data = {
                "tmsl": raw_tmsl,
                "dataset_id": raw_tmsl.get("model", {}).get("name", "test_dataset")
            }
            osi_model = converter.to_osi(source_data)

            osi_dict = osi_model.model_dump(mode="json")
            save_json(osi_dict, "3_osi_model.json", "OSI")
            save_yaml(osi_dict, "3_osi_model.yaml", "OSI")
            print(f"       Datasets: {len(osi_model.datasets)}")
            print(f"       Metrics:  {len(osi_model.metrics)}")
            print(f"       Relationships: {len(osi_model.relationships)}")
            found_any = True
        except Exception as e:
            print(f"  [WARN] Could not generate OSI model: {e}")
            # Try inferred OSI from output folder
            inferred_dir = project_root / "output" / "models"
            if inferred_dir.exists():
                for osi_file in inferred_dir.rglob("osi_inferred.json"):
                    with open(osi_file, "r", encoding="utf-8") as f:
                        osi_data = json.load(f)
                    save_json(osi_data, "3_osi_model.json", "OSI (inferred)")
                    found_any = True
                    break

    # ── Stage 4: SML Model (Step 6 Phase 2 output) ────────────────────
    print("\n--- Stage 4: SML Model (OSI → SML conversion) ---")
    if raw_tmsl_path.exists():
        try:
            from semabridge.converter.tmsl_to_sml import TMSLTransformer
            from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
            from semabridge.converter.osi_to_sml import OSIToSMLConverter

            transformer = TMSLTransformer()
            # We don't need TMSLTransformer to get OSI, we can just pass raw_tmsl to TMSLToOSIConverter.
            
            converter = TMSLToOSIConverter()
            source_data = {
                "tmsl": raw_tmsl,
                "dataset_id": raw_tmsl.get("model", {}).get("name", "test_dataset")
            }
            osi_model = converter.to_osi(source_data)

            sml_converter = OSIToSMLConverter()
            sml_model = sml_converter.from_osi(osi_model)

            sml_dict = sml_model.model_dump(mode="json")
            save_json(sml_dict, "4_sml_model.json", "SML")
            save_yaml(sml_dict, "4_sml_model.yaml", "SML")
            print(f"       Datasets:     {len(sml_model.datasets)}")
            print(f"       Metrics:      {len(sml_model.metrics)}")
            print(f"       Dimensions:   {len(sml_model.dimensions)}")
            print(f"       Relationships:{len(sml_model.relationships)}")
            found_any = True
        except Exception as e:
            print(f"  [WARN] Could not generate SML model: {e}")

    # ── Stage 4.5: CSM Model (OSI → CSM conversion) ───────────────────────
    print("\n--- Stage 4.5: CSM Model (OSI → CSM conversion) ---")
    if raw_tmsl_path.exists():
        try:
            from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
            from semabridge.converter.adapters.osi_to_csm import OSIToCSMConverter

            converter = TMSLToOSIConverter()
            source_data = {
                "tmsl": raw_tmsl,
                "dataset_id": raw_tmsl.get("model", {}).get("name", "test_dataset")
            }
            osi_model = converter.to_osi(source_data)

            csm_converter = OSIToCSMConverter()
            csm_model = csm_converter.convert(osi_model)

            csm_dict = csm_model.model_dump(mode="json")
            save_json(csm_dict, "4_csm_model.json", "CSM")
            save_yaml(csm_dict, "4_csm_model.yaml", "CSM")
            print(f"       Datasets:     {len(csm_model.datasets)}")
            print(f"       Metrics:      {len(csm_model.metrics)}")
            print(f"       Relationships:{len(csm_model.relationships)}")
            found_any = True
        except Exception as e:
            print(f"  [WARN] Could not generate CSM model: {e}")

    # ── Stage 5: SML → OSI reverse (diagnostic/inferred) ─────────────
    print("\n--- Stage 5: SML → OSI Reverse (Inferred types post-deploy) ---")
    inferred_dir = project_root / "output" / "models"
    if inferred_dir.exists():
        for osi_inferred in inferred_dir.rglob("osi_inferred.json"):
            with open(osi_inferred, "r", encoding="utf-8") as f:
                inferred_data = json.load(f)
            save_json(inferred_data, "5_sml_to_osi_inferred.json", "SML→OSI")
            found_any = True
            break
        for osi_yaml in inferred_dir.rglob("osi_inferred.yaml"):
            import shutil
            shutil.copy2(osi_yaml, OUTPUT_DIR / "5_sml_to_osi_inferred.yaml")
            print(f"  [SML→OSI] Saved: 5_sml_to_osi_inferred.yaml")
            break

    # ── Stage 6: Snowflake DDL (Step 8/9 output) ──────────────────────
    print("\n--- Stage 6: Snowflake DDL (Target deployment artifact) ---")
    if raw_tmsl_path.exists():
        try:
            from semabridge.converter.tmsl_to_sml import TMSLTransformer
            from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
            from semabridge.converter.osi_to_sml import OSIToSMLConverter
            from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

            converter = TMSLToOSIConverter()
            source_data = {
                "tmsl": raw_tmsl,
                "dataset_id": raw_tmsl.get("model", {}).get("name", "test_dataset")
            }
            osi_model = converter.to_osi(source_data)
            sml_converter = OSIToSMLConverter()
            sml_model = sml_converter.from_osi(osi_model)

            # Generate DDL without deploying
            mock_config = MagicMock()
            mock_config.database = "SAMPLE_DB"
            mock_config.schema_name = "PUBLIC"
            mock_config.naming_strategy = "deterministic_hash"

            emitter = SnowflakeEmitter(mock_config)
            ddl_statements = emitter.generate_ddls(sml_model)

            if ddl_statements:
                full_ddl = "\n\n".join(ddl_statements) if isinstance(ddl_statements, list) else str(ddl_statements)
                save_text(full_ddl, "6_snowflake_ddl.sql", "DDL")
                found_any = True
            else:
                print("  [SKIP] No DDL generated")
        except Exception as e:
            print(f"  [WARN] Could not generate DDL: {e}")

    return found_any


def print_summary():
    """Print summary of all captured files."""
    print("\n" + "=" * 70)
    print("  SUMMARY OF CAPTURED INTERMEDIATE ARTIFACTS")
    print("=" * 70)

    if not OUTPUT_DIR.exists():
        print("  No artifacts captured.")
        return

    files = sorted(OUTPUT_DIR.glob("*"))
    if not files:
        print("  No artifacts captured.")
        return

    print(f"\n  Directory: {OUTPUT_DIR}\n")
    print(f"  {'#':<4} {'Filename':<40} {'Size':>10}")
    print(f"  {'─'*4} {'─'*40} {'─'*10}")

    total_size = 0
    for i, f in enumerate(files, 1):
        size = f.stat().st_size
        total_size += size
        print(f"  {i:<4} {f.name:<40} {size:>10,} B")

    print(f"\n  Total: {len(files)} files, {total_size:,} bytes")

    print("\n  Pipeline flow captured:")
    print("  ┌─────────────────┐")
    print("  │  Fabric TMDL    │ → 1_raw_tmsl.json")
    print("  └────────┬────────┘")
    print("           ↓")
    print("  ┌─────────────────┐")
    print("  │  SourceFormat   │ → 2_source_format_summary.json")
    print("  └────────┬────────┘")
    print("           ↓")
    print("  ┌─────────────────┐")
    print("  │   OSI Model     │ → 3_osi_model.json / .yaml")
    print("  └────────┬────────┘")
    print("           ↓")
    print("  ┌─────────────────┐")
    print("  │   SML Model     │ → 4_sml_model.json / .yaml")
    print("  └────────┬────────┘")
    print("           ↓")
    print("  ┌─────────────────┐")
    print("  │   CSM Model     │ → 4_csm_model.json / .yaml")
    print("  └────────┬────────┘")
    print("           ↓")
    print("  ┌─────────────────┐")
    print("  │ SML→OSI Inferred│ → 5_sml_to_osi_inferred.json")
    print("  └────────┬────────┘")
    print("           ↓")
    print("  ┌─────────────────┐")
    print("  │ Snowflake DDL   │ → 6_snowflake_ddl.sql")
    print("  └─────────────────┘")


if __name__ == "__main__":
    # Fix encoding for Windows console
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    print("=" * 70)
    print("  SemaBridge -- Intermediate Format Capture Script")
    print("  Captures all artifacts between Extraction, OSI, SML, and Deploy")
    print("=" * 70)

    success = capture_from_existing_output()
    print_summary()

    if success:
        print("\n[SUCCESS] Intermediate artifacts captured successfully!")
    else:
        print("\n[WARNING] Some artifacts could not be captured.")
        print("   Run a sync first, then re-run this script:")
        print("   semabridge sync --source fabric --target snowflake")
