
import json
import yaml
from pathlib import Path
from typing import Any, Dict

from semabridge.sml.models import SMLModel
from semabridge.converter.sml_to_osi import SMLToOSIConverter
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

def export_sml_to_osi(sml_path: Path, output_path: Path):
    """Loads an SML YAML file and exports it as an OSI JSON file."""
    print(f"Loading SML from {sml_path}...")
    with open(sml_path, 'r') as f:
        sml_data = yaml.safe_load(f)
    
    sml_model = SMLModel(**sml_data)
    converter = SMLToOSIConverter()
    osi_model = converter.to_osi(sml_model)
    
    print(f"Saving OSI to {output_path}...")
    with open(output_path, 'w') as f:
        json.dump(osi_model.model_dump(mode='json'), f, indent=2)
    print("Export complete.")

def export_tmsl_to_osi(tmsl_path: Path, output_path: Path):
    """Loads a TMSL BIM file and exports it as an OSI JSON file."""
    print(f"Loading TMSL from {tmsl_path}...")
    with open(tmsl_path, 'r') as f:
        tmsl_data = json.load(f)
    
    converter = TMSLToOSIConverter()
    # to_osi expects a dict with 'tmsl' key
    osi_model = converter.to_osi({
        "tmsl": tmsl_data,
        "workspace_id": "manual-export",
        "dataset_id": tmsl_path.stem
    })
    
    print(f"Saving OSI to {output_path}...")
    with open(output_path, 'w') as f:
        json.dump(osi_model.model_dump(mode='json'), f, indent=2)
    print("Export complete.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python scripts/export_osi_format.py <file_path>")
        sys.exit(1)
    
    input_file = Path(sys.argv[1])
    output_file = input_file.parent / f"{input_file.stem}_osi.json"
    
    if input_file.suffix == '.yaml' or input_file.suffix == '.yml':
        export_sml_to_osi(input_file, output_file)
    elif input_file.suffix == '.bim':
        export_tmsl_to_osi(input_file, output_file)
    else:
        print(f"Unsupported file format: {input_file.suffix}. Supported: .yaml, .yml, .bim")
