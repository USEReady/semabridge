import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Set key
os.environ["Feather-Api-Key"] = "rc_fa9e3de6b45a86df264ed634f6457e2179883b21f8759e3f4d3a2095d0cee5d9"

from semabridge.converter.featherless_translator import translate_with_featherless

def test_translation():
    test_dax = "TOTALYTD(SUM(SalesFact[Units]), 'Date'[Date])"
    result = translate_with_featherless(test_dax, "Total Units YTD")
    print("\nFeatherless Translation Result:")
    print(result)

if __name__ == "__main__":
    test_translation()
