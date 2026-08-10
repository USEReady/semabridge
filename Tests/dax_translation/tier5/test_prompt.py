"""Unit tests for semabridge.dax_translation.tier5.prompt — synthetic data only."""
from semabridge.dax_translation.types import Dialect, TranslationRequest
from semabridge.dax_translation.tier5.prompt import (
    build_prompt,
    build_batch_prompt,
    build_system_message,
    parse_batch_payload,
)


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


# ---------------------------------------------------------------------------
# Part 4 follow-up: type-annotated schema context + rolling-window rule.
# Synthetic placeholder names only -- no live LLM call needed to verify any
# of this, only that the prompt-building functions produce the expected text.
# ---------------------------------------------------------------------------

def test_schema_context_is_annotated_with_declared_column_types_when_available():
    """The real prevention half of the DATE-vs-INTEGER incident fix: the
    model can only avoid the mismatch if it can actually see which columns
    are DATE-typed vs. INTEGER/NUMBER-typed. Confirms build_prompt renders
    that annotation for a synthetic model's dataset_col_types -- no live
    LLM call needed to verify this, just the prompt text itself."""
    request = _request(
        dataset_col_lookup={"SalesFact": {"MONTHINDEX", "MAX_DATE", "UNITS"}},
        dataset_aliases={"SalesFact": "salesfact"},
        dataset_name="SalesFact",
        dataset_col_types={
            "SalesFact": {"MONTHINDEX": "INTEGER", "MAX_DATE": "DATE", "UNITS": "INTEGER"},
        },
    )
    prompt = build_prompt(request)
    assert "MONTHINDEX (INTEGER)" in prompt
    assert "MAX_DATE (DATE)" in prompt
    assert "UNITS (INTEGER)" in prompt


def test_schema_context_has_no_annotation_when_types_are_unavailable():
    """No false advertising: a column with no known type renders exactly as
    it always did (bare name), not with a placeholder/empty annotation."""
    prompt = build_prompt(_request())
    assert "SOMECOLUMN (" not in prompt
    assert "OTHERCOLUMN (" not in prompt


def test_prompt_includes_rolling_window_type_safety_rule():
    """The prevention-side rule added after the DATE_ADDDAYSTODATE-vs-
    MONTHINDEX incident: rolling/trailing-window guidance must tell the
    model to keep window-boundary arithmetic in the same domain as the
    comparison column, and to never combine a DATE-producing expression
    with an INTEGER/NUMBER column."""
    prompt = build_prompt(_request())
    assert "rolling/trailing-window" in prompt
    assert "INTEGER/NUMBER" in prompt
    assert "never produce a DATE-typed expression" in prompt


def test_rolling_windows_skill_file_is_loaded_from_the_prompts_directory():
    """prompt.py's _load_skill_blocks() has always looked for
    prompts/rolling_windows.md -- it never existed until this fix. Confirms
    it's now actually picked up and included in the live prompt (run from
    the repo root, same as _load_skill_blocks()'s own default resolution)."""
    prompt = build_prompt(_request())
    assert "Rolling / trailing-window patterns" in prompt


def test_build_prompt_end_to_end_with_build_dataset_col_types_from_a_synthetic_model():
    """End-to-end reuse check: connectors/type_safety_validator.py's
    build_dataset_col_types() (the same function the DDL-emission-time
    safety net uses) can feed TranslationRequest.dataset_col_types directly
    -- no separate type-lookup mechanism needed for the prompt-side fix.
    Synthetic model only, no real project/schema."""
    from types import SimpleNamespace
    from semabridge.connectors.type_safety_validator import build_dataset_col_types

    synthetic_datasets = [
        SimpleNamespace(
            unique_name="SalesFact",
            columns=[
                SimpleNamespace(unique_name="MonthIndex", data_type="integer"),
                SimpleNamespace(unique_name="Max Date", data_type="date"),
            ],
        )
    ]
    request = _request(
        dataset_name="SalesFact",
        dataset_col_lookup={"SalesFact": {"MONTHINDEX", "MAX_DATE"}},
        dataset_aliases={"SalesFact": "salesfact"},
        dataset_col_types=build_dataset_col_types(synthetic_datasets),
    )
    prompt = build_prompt(request)
    assert "MONTHINDEX (INTEGER)" in prompt
    assert "MAX_DATE (DATE)" in prompt


def test_batch_prompt_merges_column_types_across_requests():
    """build_batch_prompt's schema context is the union of every request's
    dataset_col_lookup -- confirms dataset_col_types is unioned the same
    way, not just dropped for the batch path."""
    requests = [
        _request(
            dataset_col_lookup={"SalesFact": {"MONTHINDEX"}},
            dataset_col_types={"SalesFact": {"MONTHINDEX": "INTEGER"}},
        ),
        _request(
            dataset_col_lookup={"SalesFact": {"MAX_DATE"}},
            dataset_col_types={"SalesFact": {"MAX_DATE": "DATE"}},
        ),
    ]
    prompt = build_batch_prompt(requests)
    assert "MONTHINDEX (INTEGER)" in prompt
    assert "MAX_DATE (DATE)" in prompt


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


def test_few_shot_section_contains_no_real_customer_vocabulary():
    """The salvaged few-shot section used to embed one real customer's
    actual schema vocabulary (e.g. "Corporate DSI", "Corporate IOH",
    "GL Refresh Datetime") directly in the live prompt sent to the LLM for
    every customer's translation — a data-hygiene leak, not a customer
    name any generic example should ever need."""
    prompt = build_prompt(_request())
    for leaked_term in ("Corporate DSI", "Corporate COS", "Corporate IOH", "GL Refresh Datetime"):
        assert leaked_term not in prompt


def test_snowflake_prompt_disclaims_the_databricks_style_examples():
    """The few-shot examples are Databricks-backtick-quoted regardless of
    dialect, silently contradicting the Snowflake quoting rule stated just
    above them. A Snowflake-dialect prompt must carry an explicit note
    telling the model not to copy that quoting style verbatim."""
    prompt = build_prompt(_request(dialect=Dialect.SNOWFLAKE))
    assert "do not copy the examples' literal backtick quoting" in prompt


def test_databricks_prompt_has_no_disclaimer_since_examples_already_match():
    prompt = build_prompt(_request(dialect=Dialect.DATABRICKS))
    assert "do not copy the examples' literal backtick quoting" not in prompt


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


# ---------------------------------------------------------------------------
# parse_batch_payload -- realistic LLM output malformations, not just an
# arbitrary "not json at all" string. Each of these is a shape a real
# provider can plausibly emit: a trailing comma from a model that treats
# every list/dict entry uniformly, an unescaped quote inside a generated SQL
# string literal, and truncation from hitting its output-token cap mid-
# response (see the Tier 5 max_tokens=500-per-batch-call finding). All three
# must be treated as "this provider's whole attempt failed" (None), never
# raise, and never return a partial/best-effort dict that could be mistaken
# for a real answer.
# ---------------------------------------------------------------------------

def test_parse_batch_payload_valid_json_is_parsed():
    """Positive control: confirms the other three tests are actually
    testing malformation-handling, not a parser that rejects everything."""
    result = parse_batch_payload('{"m0": "SUM(x)", "m1": "AVG(y)"}')
    assert result == {"m0": "SUM(x)", "m1": "AVG(y)"}


def test_parse_batch_payload_rejects_trailing_comma():
    text = '{"m0": "SUM(x)", "m1": "AVG(y)",}'
    assert parse_batch_payload(text) is None


def test_parse_batch_payload_rejects_unescaped_quote_in_string_value():
    """A model that emits SQL containing a literal `"` inside a JSON string
    value without escaping it (e.g. a generated string comparison) breaks
    the enclosing JSON object at that point."""
    text = '{"m0": "SUM(CASE WHEN x="bad" THEN 1 END)", "m1": "AVG(y)"}'
    assert parse_batch_payload(text) is None


def test_parse_batch_payload_rejects_truncated_mid_object():
    """Simulates hitting the provider's output-token cap partway through
    the batch response -- the object never closes."""
    text = '{"m0": "SUM(x)", "m1": "AVG(CASE WHEN x > 1 THEN'
    assert parse_batch_payload(text) is None


def test_parse_batch_payload_rejects_truncated_mid_string_value():
    """A narrower truncation than the above: the cutoff lands inside an
    open string literal rather than between keys."""
    text = '{"m0": "SUM(x)", "m1": "AVG(CASE WHEN x > 1 THEN y ELSE'
    assert parse_batch_payload(text) is None


def test_parse_batch_payload_none_never_raises_for_any_of_these_shapes():
    """Belt-and-suspenders: whatever the specific malformation, the
    function's contract (documented in its own docstring) is "return None,
    never raise" -- confirm none of the four bad shapes above escape as an
    exception instead of a clean None."""
    bad_shapes = [
        '{"m0": "SUM(x)", "m1": "AVG(y)",}',
        '{"m0": "SUM(CASE WHEN x="bad" THEN 1 END)", "m1": "AVG(y)"}',
        '{"m0": "SUM(x)", "m1": "AVG(CASE WHEN x > 1 THEN',
        '{"m0": "SUM(x)", "m1": "AVG(CASE WHEN x > 1 THEN y ELSE',
        "",
        "   ",
        "not json at all",
    ]
    for text in bad_shapes:
        assert parse_batch_payload(text) is None
