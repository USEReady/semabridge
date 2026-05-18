"""Debug: check if supplement block is being reached and running."""
import sys, json, traceback
sys.path.insert(0, "src")
from pathlib import Path

raw = json.loads(Path("output/debug/e05c3659-5b6a-44f5-b7bf-8426f8a6f7ae/raw_fabric_model.json").read_text())

# Monkey-patch warning to catch swallowed errors
import logging
original_warning = logging.warning
suppressed = []
def capture_warning(msg, *args, **kwargs):
    suppressed.append(msg % args if args else msg)
    original_warning(msg, *args, **kwargs)
logging.warning = capture_warning

# Also patch logger specifically
import semabridge.converter.tmsl_to_osi as m
orig_warn = m.logger.warning
def capture_module_warn(msg, *args, **kwargs):
    suppressed.append(("MODULE_WARN", msg % args if args else msg))
    orig_warn(msg, *args, **kwargs)
m.logger.warning = capture_module_warn

logging.disable(logging.CRITICAL)

from semabridge.converter.tmsl_to_osi import TMDLToOSIConverter
osi = TMDLToOSIConverter().to_osi({"tmdl": raw, "workspace_id": "test", "dataset_id": "test"})

logging.disable(logging.NOTSET)
print(f"OSI relationships: {len(osi.relationships)}")
for r in osi.relationships:
    print(f"  {r.from_dataset}({r.from_columns}) -> {r.to_dataset}({r.to_columns})")

if suppressed:
    print("\nSuppressed warnings (potential errors):")
    for s in suppressed:
        print(f"  {s}")
