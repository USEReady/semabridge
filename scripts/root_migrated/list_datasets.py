#!/usr/bin/env python
"""List all datasets and prepare for batch testing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.core.settings import Settings

# Load settings
settings = Settings()

fabric_extractor = FabricExtractor(settings.fabric)
models = fabric_extractor.list_semantic_models()

print(f"\n{'='*80}")
print(f"Available Semantic Models ({len(models)} total)")
print(f"{'='*80}\n")

for i, model in enumerate(models, 1):
    display_name = model.get("displayName", "?")
    model_id = model.get("id", "?")
    print(f"{i:2}. {display_name:50} [{model_id}]")

print(f"\n{'='*80}")
print("Recommended test datasets:")
print("  - COMPETITIVE_MARKETING_ANALYSIS (larger dataset)")
print("  - Probability (already tested)")
print("  - Others for edge case coverage")
print(f"{'='*80}\n")
