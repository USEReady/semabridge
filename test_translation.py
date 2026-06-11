import sys
sys.path.append('src')
from semabridge.converter.dax_rule_translator import translate_dax

class MockResolver:
    def resolve_all_references(self, dax):
        return "TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])"

resolver = MockResolver()
print(translate_dax("TOTALYTD([Total Units], 'Date'[Date])", "SALESFACT", resolver=resolver))
