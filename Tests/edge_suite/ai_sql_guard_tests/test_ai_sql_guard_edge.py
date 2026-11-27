"""50 contract tests: Ask AI metric choice and validation of engine-generated SQL.

Contract (module semabridge.ai.sql_validator):
  find_synonym_collisions(model) -> [{"term", "metrics"}]
  assign_synonyms(model) -> {metric_name: [terms]}
  resolve_metric(question, model) -> {"metric", "alternatives"}
  validate_sql(sql, model, question="", *, allow_current_date=False) -> {"action": execute|rewrite|refuse,
        "path": governed|rewritten|free_form|none, "sql", "flags": [codes], "assumptions",
        "unmapped", "suggestion", "certifiable": bool}
  status_dimensions(model) -> [{"name": "IS_<METRIC>", "table", "predicate"}]
  verified_queries_from_cases(cases) -> [{"question", "sql"}]
Flag codes: CURRENT_DATE_ANCHOR, FREE_FORM, GENERATED_OBJECT, SNAPSHOT_GRAIN, NON_UNIQUE_JOIN, FAN_OUT,
GUESSED_RULE, HELPER_METRIC, UNKNOWN_METRIC, WRITE_STATEMENT, MULTI_STATEMENT, EMPTY_SQL.
"""
import pytest

from adapter import impl
from model_fixture import model

V = "DB.S.V"


def sv(metrics, dims=None, where=None):
    s = f"SELECT * FROM SEMANTIC_VIEW({V} METRICS {metrics}"
    if dims:
        s += f" DIMENSIONS {dims}"
    if where:
        s += f" WHERE {where}"
    return s + ")"


# =============================================================================
# A. Synonyms and metric choice (10)
# =============================================================================

def test_a01_shared_generic_synonym_is_reported_as_collision():
    cols = impl.find_synonym_collisions(model())
    assert any(c["term"].startswith("employee") and set(c["metrics"]) == {"ACTIVES", "EMPCOUNT"} for c in cols)


def test_a02_no_collision_when_synonyms_are_distinct():
    m = model()
    for x in m["metrics"]:
        if x["name"] == "ACTIVES":
            x["synonyms"] = ["active employees"]
    assert impl.find_synonym_collisions(m) == []


def test_a03_generic_term_kept_only_on_unfiltered_base_metric():
    syn = impl.assign_synonyms(model())
    assert "employees" in [s.lower() for s in syn["EMPCOUNT"]]
    assert "employees" not in [s.lower() for s in syn["ACTIVES"]]


def test_a04_filtered_metric_keeps_a_qualified_term():
    syn = impl.assign_synonyms(model())
    assert any("active" in s.lower() for s in syn["ACTIVES"])


@pytest.mark.parametrize("q", ["How many employees do we have?", "How many employees in each business unit?",
                               "employee count by gender", "What is our headcount?"])
def test_a05_generic_employee_questions_use_the_base_count(q):
    assert impl.resolve_metric(q, model())["metric"] == "EMPCOUNT"


def test_a06_active_employee_question_uses_the_filtered_metric():
    assert impl.resolve_metric("How many active employees are there?", model())["metric"] == "ACTIVES"


def test_a07_alternatives_are_listed_when_a_term_is_shared():
    r = impl.resolve_metric("How many employees do we have?", model())
    assert "ACTIVES" in r["alternatives"]


def test_a08_unknown_term_resolves_to_nothing():
    assert impl.resolve_metric("How many satellites launched?", model())["metric"] is None


# =============================================================================
# B. Today's-date anchoring in generated SQL (10)
# =============================================================================

def test_b01_current_date_year_filter_in_governed_sql_is_not_executed():
    r = impl.validate_sql(sv("NEW_HIRES", "bu.bu", "employee.date = DATE_PART('year', CURRENT_DATE)"),
                          model(), "new hires by business unit vs last year")
    assert r["action"] != "execute" and "CURRENT_DATE_ANCHOR" in r["flags"]


def test_b02_rewritten_to_period_metric_when_one_exists():
    r = impl.validate_sql(sv("NEW_HIRES", "bu.bu", "YEAR(x) = YEAR(CURRENT_DATE()) - 1"), model(), "new hires vs last year")
    assert r["action"] == "rewrite" and "NEW_HIRES_SPLY" in r["sql"] and "CURRENT_DATE" not in r["sql"].upper()


def test_b03_refused_with_suggestion_when_no_period_metric_exists():
    r = impl.validate_sql(sv("SEPS", None, "YEAR(x) = YEAR(CURRENT_DATE)"), model(), "separations this year vs last")
    assert r["action"] == "refuse" and r["suggestion"]


@pytest.mark.parametrize("fn", ["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()", "GETDATE()", "SYSDATE()", "NOW()"])
def test_b04_all_today_functions_are_detected(fn):
    r = impl.validate_sql(sv("SEPS", None, f"x >= DATEADD(YEAR, -1, {fn})"), model(), "separations last year")
    assert "CURRENT_DATE_ANCHOR" in r["flags"]


def test_b05_explicit_now_intent_from_the_engine_allows_current_date():
    r = impl.validate_sql(sv("SEPS", None, "x = CURRENT_DATE"), model(), "q", allow_current_date=True)
    assert "CURRENT_DATE_ANCHOR" not in r["flags"]


def test_b10_validator_does_not_interpret_question_words():
    # "today" in the question changes nothing; only the engine's resolved intent does.
    r = impl.validate_sql(sv("SEPS", None, "x = CURRENT_DATE"), model(), "How many separations today?")
    assert "CURRENT_DATE_ANCHOR" in r["flags"]


def test_b06_current_date_inside_a_string_is_not_flagged():
    r = impl.validate_sql(sv("SEPS", None, "note = 'CURRENT_DATE'"), model(), "separations")
    assert "CURRENT_DATE_ANCHOR" not in r["flags"]


def test_b07_column_names_containing_the_word_are_not_flagged():
    r = impl.validate_sql(sv("SEPS", "bu.bu", "CURRENT_DATE_KEY = 1"), model(), "separations")
    assert "CURRENT_DATE_ANCHOR" not in r["flags"]


def test_b08_models_without_data_anchoring_allow_current_date():
    r = impl.validate_sql(sv("SEPS", None, "x = CURRENT_DATE"), model(data_anchored=False), "separations this year")
    assert "CURRENT_DATE_ANCHOR" not in r["flags"]


def test_b09_today_anchor_in_free_form_sql_is_refused():
    sql = "SELECT COUNT(*) FROM SEPARATIONS WHERE YEAR(SEPDATE) = YEAR(current_date)"
    r = impl.validate_sql(sql, model(), "separations this year")
    assert r["action"] == "refuse" and "CURRENT_DATE_ANCHOR" in r["flags"]


# =============================================================================
# C. Governed vs free-form (8)
# =============================================================================

def test_c01_semantic_view_query_is_governed_and_certifiable():
    r = impl.validate_sql(sv("EMPCOUNT", "bu.bu"), model(), "employees by business unit")
    assert r["path"] == "governed" and r["action"] == "execute" and r["certifiable"] is True


def test_c02_base_table_average_by_dimension_is_rewritten_to_semantic_view():
    sql = "SELECT e.BU, AVG(e.AGE) FROM EMPLOYEE e GROUP BY e.BU"
    r = impl.validate_sql(sql, model(), "average age by business unit")
    assert r["action"] == "rewrite" and "SEMANTIC_VIEW" in r["sql"] and "AVG_AGE" in r["sql"]


def test_c03_partly_mappable_sql_stays_free_form_with_unmapped_parts():
    sql = "SELECT e.BU, AVG(e.AGE), MEDIAN(e.TENUREDAYS) FROM EMPLOYEE e GROUP BY e.BU"
    r = impl.validate_sql(sql, model(), "average age and median tenure by business unit")
    assert r["path"] == "free_form" and r["unmapped"]


def test_c04_object_generated_by_semabridge_is_flagged_from_metadata():
    sql = "SELECT BU, MEDIAN(AGE) FROM EMPLOYEE_ENRICHED GROUP BY BU"
    r = impl.validate_sql(sql, model(), "median age by business unit")
    assert "GENERATED_OBJECT" in r["flags"]


def test_c08_generated_flag_comes_from_metadata_not_the_name():
    m = model()
    m["tables"]["EMPLOYEE_ENRICHED"]["generated"] = False
    sql = "SELECT BU, MEDIAN(AGE) FROM EMPLOYEE_ENRICHED GROUP BY BU"
    assert "GENERATED_OBJECT" not in impl.validate_sql(sql, m, "x")["flags"]


def test_c05_free_form_answers_are_never_certifiable():
    sql = "SELECT BU, MEDIAN(AGE) FROM EMPLOYEE GROUP BY BU"
    assert impl.validate_sql(sql, model(), "median age")["certifiable"] is False


def test_c06_unknown_metric_in_semantic_view_is_refused():
    r = impl.validate_sql(sv("BADHIREPCTOFACTIVESPLY", "bu.bu"), model(), "bad hire share vs last year")
    assert r["action"] == "refuse" and "UNKNOWN_METRIC" in r["flags"]


@pytest.mark.parametrize("sql", ["DELETE FROM EMPLOYEE", "SELECT 1; DROP TABLE EMPLOYEE"])
def test_c07_write_or_multiple_statements_are_refused(sql):
    assert impl.validate_sql(sql, model(), "x")["action"] == "refuse"


# =============================================================================
# D. Grain of snapshot tables (6)
# =============================================================================

def test_d01_average_over_snapshot_rows_is_flagged():
    sql = "SELECT BU, MEDIAN(AGE) FROM EMPLOYEE GROUP BY BU"
    assert "SNAPSHOT_GRAIN" in impl.validate_sql(sql, model(), "median age")["flags"]


def test_d02_latest_row_per_entity_is_accepted():
    sql = ("SELECT BU, MEDIAN(AGE) FROM (SELECT * FROM EMPLOYEE QUALIFY ROW_NUMBER() OVER "
           "(PARTITION BY EMPLID ORDER BY DATE DESC) = 1) t GROUP BY BU")
    assert "SNAPSHOT_GRAIN" not in impl.validate_sql(sql, model(), "median age")["flags"]


def test_d03_single_period_filter_is_accepted():
    sql = "SELECT BU, MEDIAN(AGE) FROM EMPLOYEE WHERE DATE = '2014-12-31' GROUP BY BU"
    assert "SNAPSHOT_GRAIN" not in impl.validate_sql(sql, model(), "median age at year end")["flags"]


def test_d04_event_tables_are_not_flagged():
    sql = "SELECT BU, MEDIAN(EMPLID) FROM HIRES GROUP BY BU"
    assert "SNAPSHOT_GRAIN" not in impl.validate_sql(sql, model(), "x")["flags"]


def test_d05_distinct_entity_count_is_accepted():
    sql = "SELECT BU, COUNT(DISTINCT EMPLID) FROM EMPLOYEE GROUP BY BU"
    assert "SNAPSHOT_GRAIN" not in impl.validate_sql(sql, model(), "distinct employees by business unit")["flags"]


def test_d06_snapshot_grain_comes_from_metric_metadata():
    m = model()
    m["tables"]["EMPLOYEE"]["grain"] = "unknown"   # only the non-additive metric says it is a snapshot
    sql = "SELECT BU, MEDIAN(AGE) FROM EMPLOYEE GROUP BY BU"
    assert "SNAPSHOT_GRAIN" in impl.validate_sql(sql, m, "median age")["flags"]


# =============================================================================
# E. Joins (6)
# =============================================================================

def test_e01_join_on_unique_key_is_accepted():
    sql = "SELECT b.REGION, COUNT(DISTINCT e.EMPLID) FROM EMPLOYEE e JOIN BU b ON e.BU = b.BU GROUP BY b.REGION"
    assert "NON_UNIQUE_JOIN" not in impl.validate_sql(sql, model(), "employees by region")["flags"]


def test_e02_join_on_non_unique_key_is_flagged():
    sql = "SELECT h.BU, COUNT(*) FROM HIRES h JOIN EMPLOYEE e ON h.BU = e.BU GROUP BY h.BU"
    assert "NON_UNIQUE_JOIN" in impl.validate_sql(sql, model(), "hires")["flags"]


def test_e03_join_with_unknown_uniqueness_is_flagged():
    sql = "SELECT s.REASON, COUNT(*) FROM SEPARATIONS s JOIN HIRES h ON s.EMPLID = h.EMPLID GROUP BY s.REASON"
    assert "NON_UNIQUE_JOIN" in impl.validate_sql(sql, model(), "separations by reason")["flags"]


def test_e04_two_fact_tables_through_a_shared_dimension_is_fan_out():
    sql = ("SELECT b.BU, COUNT(h.EMPLID), COUNT(s.EMPLID) FROM HIRES h JOIN BU b ON h.BU = b.BU "
           "JOIN SEPARATIONS s ON s.BU = b.BU GROUP BY b.BU")
    r = impl.validate_sql(sql, model(), "hires and separations by business unit")
    assert "FAN_OUT" in r["flags"] and r["action"] == "refuse"


def test_e05_fan_out_answers_are_never_certifiable():
    sql = "SELECT COUNT(*) FROM HIRES h JOIN SEPARATIONS s ON h.EMPLID = s.EMPLID"
    assert impl.validate_sql(sql, model(), "x")["certifiable"] is False


def test_e06_single_fact_with_dimension_is_not_fan_out():
    sql = "SELECT b.REGION, COUNT(h.EMPLID) FROM HIRES h JOIN BU b ON h.BU = b.BU GROUP BY b.REGION"
    assert "FAN_OUT" not in impl.validate_sql(sql, model(), "hires by region")["flags"]


# =============================================================================
# F. Business-rule definitions and helper metrics (5)
# =============================================================================

def test_f01_condition_matching_a_model_definition_is_not_a_guess():
    sql = "SELECT BU, MEDIAN(AGE) FROM EMPLOYEE WHERE TERMDATE IS NULL AND DATE = '2014-12-31' GROUP BY BU"
    r = impl.validate_sql(sql, model(), "median age of active employees")
    assert "GUESSED_RULE" not in r["flags"] or not any("TERMDATE IS NULL" in a.upper() and "assumed" in a.lower() for a in r["assumptions"])


def test_f02_invented_status_rule_is_reported_as_assumption():
    sql = ("SELECT BU, CASE WHEN TERMDATE > DATE THEN 'Active' ELSE 'Separated' END AS s, MEDIAN(AGE) "
           "FROM EMPLOYEE WHERE DATE = '2014-12-31' GROUP BY BU, s")
    r = impl.validate_sql(sql, model(), "median age active vs separated")
    assert "GUESSED_RULE" in r["flags"] and r["assumptions"]


def test_f03_guessed_answers_are_not_certifiable():
    sql = "SELECT COUNT(*) FROM EMPLOYEE WHERE GENDER = 'F' AND DATE = '2014-12-31'"
    r = impl.validate_sql(sql, model(), "female employees")
    assert r["certifiable"] is False


def test_f04_helper_metric_flagged_when_business_metric_exists():
    r = impl.validate_sql(sv("SUM_OF_BADHIRES"), model(), "How many bad hires?")
    assert "HELPER_METRIC" in r["flags"] and r["certifiable"] is False


def test_f05_helper_metric_not_flagged_without_alternative():
    r = impl.validate_sql(sv("SUM_OF_AGE"), model(), "sum of age")
    assert "HELPER_METRIC" not in r["flags"]


# =============================================================================
# G. Governed status dimensions from filtered measures (6)
# =============================================================================

def test_g01_row_level_filter_on_own_table_becomes_a_status_dimension():
    dims = impl.status_dimensions(model(), enabled=True)
    assert {"name": "IS_ACTIVES", "table": "EMPLOYEE", "predicate": "TERMDATE IS NULL"} in dims


def test_g02_filter_on_another_table_creates_no_dimension():
    assert all(d["name"] != "IS_LATE_STARTERS" for d in impl.status_dimensions(model(), enabled=True))


def test_g03_aggregate_filter_creates_no_dimension():
    assert all(d["name"] != "IS_MAX_AGE_FLAG" for d in impl.status_dimensions(model(), enabled=True))


def test_g04_todays_date_predicate_creates_no_status_dimension():
    m = model()
    m["metrics"].append({
        "name": "RECENT_HIRES",
        "table": "HIRES",
        "agg": "COUNT",
        "column": None,
        "filter": "HIREDATE >= DATEADD(YEAR, -1, CURRENT_DATE)",
        "kind": "base",
    })
    dims = impl.status_dimensions(m, enabled=True)
    assert all(d["name"] != "IS_RECENT_HIRES" for d in dims)


def test_g05_flag_off_creates_no_status_dimensions_and_no_metadata_dimensions():
    m = model()
    assert impl.status_dimensions(m, enabled=False) == []
    from types import SimpleNamespace
    mock_domain = SimpleNamespace(metrics=[], concepts=[], relationships=[], semantic_view_name="DB.S.V")
    meta = impl.metadata_from_domain(mock_domain, deployed_status_dimensions=None)
    assert not any(d["name"].startswith("is_") or d["name"].split(".")[-1].startswith("is_") for d in meta["dimensions"])


def test_g06_sanitizer_output_and_existing_column_clash_skipped():
    m = model()
    m["metrics"].append({
        "name": "BadHire%ofActives",
        "table": "EMPLOYEE",
        "agg": "COUNT",
        "column": None,
        "filter": "TERMDATE IS NULL",
        "kind": "base",
    })
    dims = impl.status_dimensions(m, enabled=True)
    assert any(d["name"] == "IS_BADHIREPCTOFACTIVES" for d in dims)
    assert not any("%" in d["name"] for d in dims)

    # Clash with existing column is skipped
    clash_dims = impl.status_dimensions(m, enabled=True, existing_columns={"IS_ACTIVES"})
    assert all(d["name"] != "IS_ACTIVES" for d in clash_dims)


# =============================================================================
# H. Verified queries (2)
# =============================================================================

def test_h01_only_certified_cases_with_power_bi_match_become_verified_queries():
    cases = [{"question": "How many employees?", "sql": sv("EMPCOUNT"), "status": "certified", "power_bi_match": True},
             {"question": "How many hires?", "sql": sv("NEW_HIRES"), "status": "approved", "power_bi_match": True},
             {"question": "How many seps?", "sql": sv("SEPS"), "status": "certified", "power_bi_match": False}]
    assert [v["question"] for v in impl.verified_queries_from_cases(cases)] == ["How many employees?"]


def test_h02_duplicate_questions_are_registered_once():
    c = {"question": "How many employees?", "sql": sv("EMPCOUNT"), "status": "certified", "power_bi_match": True}
    d = dict(c, question="  how many   employees? ")
    assert len(impl.verified_queries_from_cases([c, d])) == 1


# =============================================================================
# I. Question coverage (3)
# =============================================================================

def test_i01_dropped_split_and_metric_refuse_the_answer():
    cov = {"requested_metrics": ["AVG_AGE", "AVG_TENURE_DAYS"], "resolved_metrics": ["AVG_AGE"],
           "requested_dimensions": ["bu.bu"], "resolved_dimensions": ["bu.bu"],
           "requested_splits": ["status"], "resolved_splits": []}
    r = impl.validate_sql(sv("AVG_AGE", "bu.bu"), model(), "q", coverage=cov)
    assert r["action"] == "refuse" and "INCOMPLETE_ANSWER" in r["flags"]
    assert any("AVG_TENURE_DAYS".casefold() in u for u in r["unmapped"]) and any("status" in u for u in r["unmapped"])


def test_i02_unresolved_phrase_reported_by_engine_refuses():
    r = impl.validate_sql(sv("AVG_AGE", "bu.bu"), model(), "q", coverage={"unresolved": ["<phrase>"]})
    assert r["action"] == "refuse" and not r["certifiable"]


def test_i03_complete_coverage_is_unaffected():
    cov = {"requested_metrics": ["AVG_AGE"], "resolved_metrics": ["avg_age"],
           "requested_dimensions": ["bu.bu"], "resolved_dimensions": ["BU.BU"]}
    r = impl.validate_sql(sv("AVG_AGE", "bu.bu"), model(), "q", coverage=cov)
    assert r["action"] == "execute" and r["certifiable"] is True


# =============================================================================
# J. Rewrite safety (6) - a rewrite must keep the meaning and can never inject SQL
# =============================================================================

def test_j01_where_filter_prevents_free_form_rewrite():
    sql = "SELECT e.BU, AVG(e.AGE) FROM EMPLOYEE e WHERE e.GENDER = 'F' GROUP BY e.BU"
    r = impl.validate_sql(sql, model(), "average age of women by business unit")
    assert r["action"] != "rewrite" and r["sql"] == sql


def test_j02_extra_filter_prevents_today_anchor_rewrite():
    sql = sv("NEW_HIRES", "bu.bu", "YEAR(x) = YEAR(CURRENT_DATE()) - 1 AND employee.gender = 'F'")
    r = impl.validate_sql(sql, model(), "q")
    assert r["action"] == "refuse"


@pytest.mark.parametrize("view", ["DB.S.V) METRICS x); DROP TABLE t; --", "DB.S.V; DELETE FROM t", None, "", "A.B.C.D"])
def test_j03_unsafe_or_missing_view_name_never_rewrites(view):
    sql = "SELECT e.BU, AVG(e.AGE) FROM EMPLOYEE e GROUP BY e.BU"
    r = impl.validate_sql(sql, model(view=view), "q")
    assert r["action"] != "rewrite"
    assert "DROP" not in r["sql"].upper() and "DELETE" not in r["sql"].upper()


def test_j04_no_semantic_view_rewrite_for_other_platforms():
    sql = "SELECT e.BU, AVG(e.AGE) FROM EMPLOYEE e GROUP BY e.BU"
    r = impl.validate_sql(sql, model(platform="databricks"), "q")
    assert r["action"] != "rewrite"


def test_j05_rewritten_sql_validates_as_clean_governed():
    sql = "SELECT e.BU, AVG(e.AGE) FROM EMPLOYEE e GROUP BY e.BU"
    r = impl.validate_sql(sql, model(), "q")
    assert r["action"] == "rewrite"
    again = impl.validate_sql(r["sql"], model(), "q")
    assert again["action"] == "execute" and again["path"] == "governed" and not again["flags"]


def test_j06_limit_or_having_prevents_rewrite():
    for sql in ("SELECT e.BU, AVG(e.AGE) FROM EMPLOYEE e GROUP BY e.BU LIMIT 5",
                "SELECT e.BU, AVG(e.AGE) FROM EMPLOYEE e GROUP BY e.BU HAVING AVG(e.AGE) > 30"):
        assert impl.validate_sql(sql, model(), "q")["action"] != "rewrite"
