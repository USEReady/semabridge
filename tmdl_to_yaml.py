import os
import sys
import yaml
from pathlib import Path
from semabridge.converter.tmdl_to_osi import TMDLToOSIConverter

def convert_tmdl_folder_to_yaml(tmdl_folder_path: str, output_yaml_path: str):
    """Reads all .tmdl files from a folder, converts them to OSI, and saves as YAML."""
    folder = Path(tmdl_folder_path)
    if not folder.exists() or not folder.is_dir():
        print(f"Error: Folder '{tmdl_folder_path}' does not exist.")
        return

    print(f"Reading TMDL files from: {folder}")
    tmdl_files = {}
    
    # Read all TMDL files recursively
    for filepath in folder.rglob("*.tmdl"):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                # Store relative path
                rel_path = str(filepath.relative_to(folder)).replace("\\", "/")
                tmdl_files[rel_path] = content
                print(f"  Loaded: {rel_path}")
        except Exception as e:
            print(f"  Error reading {filepath.name}: {e}")

    if not tmdl_files:
        print("No .tmdl files found in the folder.")
        return

    source_data = {
        "tmdl": tmdl_files,
        "dataset_id": folder.name
    }

    try:
        print("\nConverting TMDL to OSI format...")
        converter = TMDLToOSIConverter()
        osi_model = converter.to_osi(source_data)
        
        # Save to YAML
        osi_dict = osi_model.model_dump(mode="json")
        out_path = Path(output_yaml_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(osi_dict, f, sort_keys=False, default_flow_style=False)
            
        print(f"\n[SUCCESS] Saved OSI Model to YAML: {out_path}")
        print(f"  Datasets: {len(osi_model.datasets)}")
        print(f"  Metrics: {len(osi_model.metrics)}")
        
    except Exception as e:
        print(f"\n[ERROR] Conversion failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tmdl_to_yaml.py <path_to_tmdl_folder> [output_file.yaml]")
        sys.exit(1)
        
    tmdl_folder = sys.argv[1]
    out_file = sys.argv[2] if len(sys.argv) > 2 else "output_tmdl_to_osi.yaml"
    
    convert_tmdl_folder_to_yaml(tmdl_folder, out_file)
