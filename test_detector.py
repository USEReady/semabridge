"""Test RelationshipDetector with actual Fabric-format column names (spaces)."""
import sys
sys.path.insert(0, "src")
import logging; logging.disable(logging.CRITICAL)

from semabridge.connectors.relationship_detector import RelationshipDetector

tables = {
    "Fact": {}, "BU": {}, "Calendar": {}, "Scenario": {}, "Product": {},
    "Customer": {}, "Industry": {}, "Executive": {}, "State": {}
}
# Column names as they appear in OSI (converted from Fabric TMDL with spaces)
columns = {
    "Fact": [
        {"name": "Customer Key"}, {"name": "Product Key"}, {"name": "BU Key"},
        {"name": "Scenario Key"}, {"name": "Revenue"}, {"name": "Material Costs"},
        {"name": "Labor Costs Variable"}, {"name": "Taxes"}, {"name": "YearPeriod"},
    ],
    "BU": [{"name": "BU Key"}, {"name": "BU"}, {"name": "Division"}, {"name": "Executive_id"}],
    "Calendar": [{"name": "YearPeriod"}, {"name": "Year"}, {"name": "Period"}, {"name": "Date"}, {"name": "Month"}, {"name": "QtrID"}],
    "Scenario": [{"name": "Scenario Key"}, {"name": "Scenario"}],
    "Product": [{"name": "Product Key"}, {"name": "Product"}],
    "Customer": [{"name": "Customer"}, {"name": "Name"}, {"name": "City"}, {"name": "Postal Code"}, {"name": "State"}, {"name": "Industry ID"}, {"name": "Country Region"}],
    "Industry": [{"name": "ID"}, {"name": "Industry"}],
    "Executive": [{"name": "ID"}, {"name": "Name"}],
    "State": [{"name": "StateCode"}, {"name": "State"}, {"name": "Region"}],
}
# PKs as set by the TMDL→OSI converter
pks = {
    "Fact": ["Customer Key", "Product Key", "BU Key", "Scenario Key"],
    "BU": ["BU Key", "Executive_id"],
    "Calendar": ["QtrID"],  # YearPeriod is NOT the PK in Fabric model
    "Scenario": ["Scenario Key"],
    "Product": ["Product Key"],
    "Customer": ["Industry ID"],  # incorrect but what the model has
    "Industry": ["ID"],
    "Executive": ["ID"],
    "State": [],
}

det = RelationshipDetector(tables, columns, pks)
rels = det.detect_all()
print(f"Detected {len(rels)} relationships:")
for r in rels:
    print(f"  {r['from_table']}({r['from_column']}) -> {r['to_table']}({r['to_column']}) [{r['source']}]")

print("\nStatus of expected relationships:")
expected = [
    ("Fact", "BU Key", "BU", "BU Key"),
    ("Fact", "Scenario Key", "Scenario", "Scenario Key"),
    ("Fact", "Product Key", "Product", "Product Key"),
    ("Fact", "YearPeriod", "Calendar", "YearPeriod"),
    ("BU", "Executive_id", "Executive", "ID"),
    ("Customer", "Industry ID", "Industry", "ID"),
]
detected_keys = {
    (r["from_table"].upper(), r["from_column"].replace(" ", "_").upper(),
     r["to_table"].upper(), r["to_column"].replace(" ", "_").upper())
    for r in rels
}
for ft, fc, tt, tc in expected:
    key = (ft.upper(), fc.replace(" ", "_").upper(), tt.upper(), tc.replace(" ", "_").upper())
    status = "FOUND" if key in detected_keys else "MISSING"
    print(f"  {status}: {ft}({fc}) -> {tt}({tc})")
