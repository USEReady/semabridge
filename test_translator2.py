import sys
sys.path.append(r'c:\Users\MANOJ\dev-test\semabridge\src')
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifier_normalizer import IdentifierNormalizer

class DummyId:
    def sanitize_column(self, col):
        return col.strip("[]'\"").upper()
    def resolve_dot_notation(self, expr, alias_by_raw, dataset_col_lookup, dataset_aliases):
        return expr

translator = MetricExpressionTranslator(behavior='LLM_ONLY')
translator._id = DummyId()

class DummyMetric:
    def __init__(self, expression, dataset):
        self.expression = expression
        self.dataset = dataset
        self.unique_name = "Dummy"

metric = DummyMetric("TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])", "SalesFact")

dataset_col_lookup = {
    'SalesFact': {'UNITS', 'REVENUE', 'PRODUCTID'},
    'Date': {'DATE', 'MONTH', 'YEAR'}
}
dataset_aliases = {
    'SalesFact': 'SALESFACT',
    'Date': 'COL_DATE'
}

print(translator._try_basic_dax_metric_fallback_expression(metric, 'SALESFACT', dataset_col_lookup))
