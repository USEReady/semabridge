"""Generic model metadata used by the tests (shape of what SemaBridge already knows after a sync).

Names mirror an HR-style model only so the scenarios read naturally; the code under test must not
contain any of them.
"""
import copy

MODEL = {
    "view": "DB.S.V",
    "platform": "snowflake",
    "data_anchored": True,
    "latest_data_date": "2014-12-31",
    "tables": {
        "EMPLOYEE": {"grain": "snapshot", "snapshot_date": "DATE", "entity_key": "EMPLID",
                     "columns": ["EMPLID", "DATE", "BU", "AGE", "TENUREDAYS", "TERMDATE", "GENDER", "BADHIRES"]},
        "BU": {"grain": "dimension", "columns": ["BU", "REGION"]},
        "HIRES": {"grain": "event", "columns": ["EMPLID", "HIREDATE", "BU"]},
        "SEPARATIONS": {"grain": "event", "columns": ["EMPLID", "SEPDATE", "BU", "REASON"]},
        "EMPLOYEE_ENRICHED": {"grain": "snapshot", "snapshot_date": "DATE", "entity_key": "EMPLID",
                              "generated": True, "source_table": "EMPLOYEE",
                              "columns": ["EMPLID", "DATE", "BU", "AGE", "TENUREDAYS", "TERMDATE"]},
    },
    "relationships": [
        {"from": "EMPLOYEE", "from_col": "BU", "to": "BU", "to_col": "BU", "cardinality": "N:1"},
        {"from": "HIRES", "from_col": "BU", "to": "BU", "to_col": "BU", "cardinality": "N:1"},
        {"from": "SEPARATIONS", "from_col": "BU", "to": "BU", "to_col": "BU", "cardinality": "N:1"},
    ],
    "key_uniqueness": {"BU.BU": {"rows": 10, "distinct": 10}, "EMPLOYEE.BU": {"rows": 900, "distinct": 10}},
    "metrics": [
        {"name": "EMPCOUNT", "label": "Employee Count", "table": "EMPLOYEE", "agg": "COUNT", "column": None,
         "filter": None, "non_additive_by": ["DATE"], "kind": "base",
         "synonyms": ["employees", "headcount", "employee count"]},
        {"name": "ACTIVES", "label": "Active Employees", "table": "EMPLOYEE", "agg": "COUNT", "column": None,
         "filter": "TERMDATE IS NULL", "non_additive_by": ["DATE"], "kind": "base",
         "synonyms": ["active employees", "employees"]},
        {"name": "AVG_AGE", "label": "Average Age", "table": "EMPLOYEE", "agg": "AVG", "column": "AGE",
         "filter": None, "kind": "base", "synonyms": ["average age"]},
        {"name": "AVG_TENURE_DAYS", "label": "Average Tenure Days", "table": "EMPLOYEE", "agg": "AVG",
         "column": "TENUREDAYS", "filter": None, "kind": "base", "synonyms": ["average tenure"]},
        {"name": "NEW_HIRES", "label": "New Hires", "table": "HIRES", "agg": "COUNT", "column": None,
         "filter": None, "kind": "base", "synonyms": ["new hires", "hires"]},
        {"name": "NEW_HIRES_SPLY", "label": "New Hires Same Period Last Year", "table": "HIRES", "agg": "COUNT",
         "column": None, "filter": None, "kind": "period", "base_metric": "NEW_HIRES", "shape": "SPLY",
         "synonyms": ["new hires last year"]},
        {"name": "SEPS", "label": "Separations", "table": "SEPARATIONS", "agg": "COUNT", "column": None,
         "filter": None, "kind": "base", "synonyms": ["separations"]},
        {"name": "BAD_HIRES", "label": "Bad Hires", "table": "EMPLOYEE", "agg": "SUM", "column": "BADHIRES",
         "filter": None, "kind": "base", "synonyms": ["bad hires"]},
        {"name": "SUM_OF_BADHIRES", "label": "SUM_OF_BADHIRES", "table": "EMPLOYEE", "agg": "SUM",
         "column": "BADHIRES", "filter": None, "kind": "helper", "synonyms": []},
        {"name": "SUM_OF_AGE", "label": "SUM_OF_AGE", "table": "EMPLOYEE", "agg": "SUM", "column": "AGE",
         "filter": None, "kind": "helper", "synonyms": []},
        {"name": "LATE_STARTERS", "label": "Late Starters", "table": "EMPLOYEE", "agg": "COUNT", "column": None,
         "filter": "BU.REGION = 'X'", "kind": "base", "synonyms": ["late starters"]},
        {"name": "MAX_AGE_FLAG", "label": "Max Age Flag", "table": "EMPLOYEE", "agg": "COUNT", "column": None,
         "filter": "AGE = MAX(AGE)", "kind": "base", "synonyms": []},
    ],
    "dimensions": [
        {"name": "bu.bu", "table": "BU", "column": "BU"},
        {"name": "employee.gender", "table": "EMPLOYEE", "column": "GENDER"},
        {"name": "employee.termdate", "table": "EMPLOYEE", "column": "TERMDATE"},
    ],
}


def model(**overrides):
    m = copy.deepcopy(MODEL)
    m.update(overrides)
    return m
