"""Unit tests for semabridge.dax_translation.tier5.prompt — synthetic data only."""
from semabridge.dax_translation.types import Dialect, TranslationRequest
from semabridge.dax_translation.tier5.prompt import build_prompt, build_system_message


def _request(**overrides):
    defaults = dict(
        dax="SUM('SomeTable'[SomeColumn])",
        dataset_name="SomeTable",
        table_alias="sometable",
        dataset_col_lookup={"SomeTable": {"SOMECOLUMN", "OTHERCOLUMN"}},
        dataset_aliases={"SomeTable": "sometable"},
        metric_name="Metric_A",
    )
    defaults.update(overrides)
    return TranslationRequest(**defaults)


def test_prompt_includes_schema_context():
    prompt = build_prompt(_request())
    assert "SomeTable" in prompt
    assert "SOMECOLUMN" in prompt
    assert "OTHERCOLUMN" in prompt


def test_prompt_includes_dax_and_metric_name():
    prompt = build_prompt(_request())
    assert "SUM('SomeTable'[SomeColumn])" in prompt
    assert "Metric_A" in prompt


def test_prompt_states_snowflake_dialect():
    prompt = build_prompt(_request(dialect=Dialect.SNOWFLAKE))
    assert "Snowflake Semantic View METRICS clause" in prompt


def test_prompt_states_databricks_dialect_when_parameterized():
    prompt = build_prompt(_request(dialect=Dialect.DATABRICKS))
    assert "Databricks SQL expression" in prompt
    assert "Snowflake Semantic View METRICS clause" not in prompt


def test_prompt_includes_salvaged_few_shot_section():
    prompt = build_prompt(_request())
    assert "CALCULATE RULES" in prompt
    assert "FEW-SHOT EXAMPLES" in prompt
    # one of the 9 salvaged worked examples
    assert "DAX: TODAY()" in prompt


def test_system_message_differs_by_dialect():
    snowflake_msg = build_system_message(Dialect.SNOWFLAKE)
    databricks_msg = build_system_message(Dialect.DATABRICKS)
    assert "Snowflake" in snowflake_msg
    assert "Databricks" in databricks_msg
    assert snowflake_msg != databricks_msg


def test_system_message_accepts_plain_string_dialect():
    assert build_system_message("snowflake") == build_system_message(Dialect.SNOWFLAKE)


def test_databricks_prompt_explicitly_instructs_backtick_quoting():
    """Step 4 (Pipeline C) finding: the rules text used to say 'quote
    identifiers only when needed' without ever naming Databricks' actual
    quote character, so the LLM had no instruction to use backticks."""
    prompt = build_prompt(_request(dialect=Dialect.DATABRICKS))
    assert "backtick" in prompt.lower()


def test_snowflake_prompt_still_instructs_double_quote_style():
    prompt = build_prompt(_request(dialect=Dialect.SNOWFLAKE))
    assert 'ALIAS."COLUMN"' in prompt
