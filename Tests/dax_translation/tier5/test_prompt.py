"""Unit tests for semabridge.dax_translation.tier5.prompt — synthetic data only."""
from semabridge.dax_translation.types import Dialect, TranslationRequest
from semabridge.dax_translation.tier5.prompt import (
    build_prompt,
    build_batch_prompt,
    build_system_message,
    build_batch_system_message,
    parse_batch_payload,
    parse_structured_response,
    _extract_sql_and_confidence,
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


def test_prompt_has_no_reachable_tables_when_none_supplied():
    """None (the default) preserves existing behavior exactly -- no
    cross-table alias is ever offered."""
    prompt = build_prompt(_request())
    assert "<none other than the default table above>" in prompt


def test_prompt_lists_reachable_table_and_its_alias_when_supplied():
    request = _request(reachable_table_aliases={"Product": "product"})
    prompt = build_prompt(request)
    assert "Product (alias: product)" in prompt
    assert "<none other than the default table above>" not in prompt


def test_verification_checklist_explains_how_to_use_reachable_tables():
    prompt = build_prompt(_request())
    assert "Tables reachable via a declared relationship" in prompt


def test_batch_prompt_includes_per_metric_reachable_tables_field():
    """Deliberately per-metric (not merged like the schema context) --
    different metrics in one batch can have different base datasets, each
    with a different reachable-table set."""
    requests = [
        _request(metric_name="Metric_A", reachable_table_aliases={"Product": "product"}),
        _request(metric_name="Metric_B"),
    ]
    prompt = build_batch_prompt(requests)
    assert '"reachable_tables": {"Product": "product"}' in prompt
    assert '"reachable_tables": {}' in prompt


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


def test_parse_batch_payload_recovers_fully_despite_trailing_comma():
    """A harmless trailing comma before the closing brace is not truncation
    at all -- both entries are complete. Real incident this whole salvage
    path exists for: discarding a fully-valid batch over one stray comma
    would be strictly worse than tolerating it. See
    _salvage_partial_batch_json."""
    text = '{"m0": "SUM(x)", "m1": "AVG(y)",}'
    assert parse_batch_payload(text) == {"m0": "SUM(x)", "m1": "AVG(y)"}


def test_parse_batch_payload_unescaped_quote_salvages_only_the_corrupted_first_entry():
    """A model that emits SQL containing a literal `"` inside a JSON string
    value without escaping it (e.g. a generated string comparison) breaks
    the enclosing JSON at that exact point -- unlike a token-cap
    truncation (which lands at the END of the response), this is an
    internal corruption, so the scanner has no reliable way to resync
    afterward and correctly stops rather than guessing. The recovered
    value for the corrupted key is itself garbage (cut at the embedded
    quote) -- Tier5Service's per-metric validation is what actually
    catches this downstream (see
    test_service.py's ..._salvage_produces_garbage_that_per_metric_validation_still_rejects),
    not this function. m1, entirely after the corruption point, is lost."""
    text = '{"m0": "SUM(CASE WHEN x="bad" THEN 1 END)", "m1": "AVG(y)"}'
    assert parse_batch_payload(text) == {"m0": "SUM(CASE WHEN x="}


def test_parse_batch_payload_recovers_leading_entries_from_truncated_mid_object():
    """Simulates hitting the provider's output-token cap partway through
    the batch response -- the object never closes. Real incident: a
    13-metric batch cut off this way used to discard all 13; now the
    complete entries before the cutoff are recovered."""
    text = '{"m0": "SUM(x)", "m1": "AVG(CASE WHEN x > 1 THEN'
    assert parse_batch_payload(text) == {"m0": "SUM(x)"}


def test_parse_batch_payload_recovers_leading_entries_from_truncated_mid_string_value():
    """A narrower truncation than the above: the cutoff lands inside an
    open string literal rather than between keys. Same recovery."""
    text = '{"m0": "SUM(x)", "m1": "AVG(CASE WHEN x > 1 THEN y ELSE'
    assert parse_batch_payload(text) == {"m0": "SUM(x)"}


def test_parse_batch_payload_recovers_many_leading_entries_from_a_realistic_truncated_batch():
    """Closer to the real, live incident this session found: a batch of
    several metrics with realistic (properly-escaped) quoted-identifier
    SQL, cut off by an output-token cap partway through. Every complete
    entry before the cutoff must be recovered, not just discarded because
    the response never closed its outer object."""
    import json as _json

    entries = {
        f"m{i}": {"sql": f'SUM(salesfact."UNITS_{i}")', "confidence": 0.8}
        for i in range(5)
    }
    full_response = _json.dumps(entries)
    # Simulate a hard cutoff partway through the 4th entry's value.
    cutoff = full_response.index('"m4"') + len('"m4": {"sql": "SUM(sale')
    truncated = full_response[:cutoff]

    parsed = parse_batch_payload(truncated)
    assert parsed is not None
    # The first 4 entries (m0-m3) were fully written before the cutoff.
    for i in range(4):
        assert parsed[f"m{i}"] == {"sql": f'SUM(salesfact."UNITS_{i}")', "confidence": 0.8}
    assert "m4" not in parsed  # cut off mid-value -- correctly not recovered


def test_parse_batch_payload_never_raises_for_any_of_these_shapes():
    """Belt-and-suspenders: whatever the specific malformation, the
    function's contract is "never raise" -- confirm none of these shapes
    escape as an exception. Since _salvage_partial_batch_json, the return
    value for a malformed-but-partially-recoverable shape is now a
    (possibly partial) dict rather than always None -- see the dedicated
    tests above for exact per-shape expectations. Only a shape with truly
    nothing recoverable (empty/whitespace/no '{' at all) still returns
    None."""
    recoverable_shapes = [
        '{"m0": "SUM(x)", "m1": "AVG(y)",}',
        '{"m0": "SUM(CASE WHEN x="bad" THEN 1 END)", "m1": "AVG(y)"}',
        '{"m0": "SUM(x)", "m1": "AVG(CASE WHEN x > 1 THEN',
        '{"m0": "SUM(x)", "m1": "AVG(CASE WHEN x > 1 THEN y ELSE',
    ]
    for text in recoverable_shapes:
        result = parse_batch_payload(text)  # must not raise
        assert isinstance(result, dict) and result  # something was salvaged

    nothing_recoverable_shapes = ["", "   ", "not json at all"]
    for text in nothing_recoverable_shapes:
        assert parse_batch_payload(text) is None


# ---------------------------------------------------------------------------
# Self-reported confidence contract: build_prompt()/build_batch_prompt()
# request {"sql":..., "confidence":...} (one-off-experiment-turned-real
# feature); parse_structured_response()/_extract_sql_and_confidence() parse
# it back out, tolerating a provider that ignores the format and returns
# bare SQL instead.
# ---------------------------------------------------------------------------

def test_single_prompt_requests_the_sql_and_confidence_json_contract():
    prompt = build_prompt(_request())
    assert '"sql"' in prompt
    assert '"confidence"' in prompt


def test_single_system_message_requests_the_sql_and_confidence_json_contract():
    message = build_system_message(Dialect.SNOWFLAKE)
    assert "sql" in message
    assert "confidence" in message


def test_batch_prompt_requests_the_sql_and_confidence_json_contract_per_key():
    prompt = build_batch_prompt([_request()])
    assert '"sql"' in prompt
    assert '"confidence"' in prompt


def test_batch_system_message_requests_the_sql_and_confidence_json_contract():
    message = build_batch_system_message(Dialect.SNOWFLAKE)
    assert "sql" in message
    assert "confidence" in message


def test_extract_sql_and_confidence_from_the_requested_dict_shape():
    sql, confidence = _extract_sql_and_confidence({"sql": "SUM(x)", "confidence": 0.82})
    assert sql == "SUM(x)"
    assert confidence == 0.82


def test_extract_sql_and_confidence_tolerates_a_bare_string_value():
    """A provider that ignores the {"sql":..., "confidence":...} contract
    and returns bare SQL instead is tolerated, not rejected -- confidence
    is None, never guessed."""
    sql, confidence = _extract_sql_and_confidence("SUM(x)")
    assert sql == "SUM(x)"
    assert confidence is None


def test_extract_sql_and_confidence_missing_confidence_key_is_none_not_a_default():
    sql, confidence = _extract_sql_and_confidence({"sql": "SUM(x)"})
    assert sql == "SUM(x)"
    assert confidence is None


def test_extract_sql_and_confidence_non_numeric_confidence_is_none():
    """A malformed confidence value must never crash SQL extraction --
    only the confidence half is discarded."""
    sql, confidence = _extract_sql_and_confidence({"sql": "SUM(x)", "confidence": "very confident"})
    assert sql == "SUM(x)"
    assert confidence is None


def test_extract_sql_and_confidence_bool_confidence_is_rejected_not_coerced():
    """bool is a subclass of int in Python -- must not silently become 0.0/1.0."""
    sql, confidence = _extract_sql_and_confidence({"sql": "SUM(x)", "confidence": True})
    assert sql == "SUM(x)"
    assert confidence is None


def test_extract_sql_and_confidence_unrecognized_shape_returns_none_none():
    sql, confidence = _extract_sql_and_confidence(12345)
    assert sql is None
    assert confidence is None


def test_parse_structured_response_valid_json_contract():
    sql, confidence = parse_structured_response('{"sql": "SUM(x)", "confidence": 0.75}')
    assert sql == "SUM(x)"
    assert confidence == 0.75


def test_parse_structured_response_tolerates_bare_sql_when_provider_ignores_format():
    """The exact backward-compatible case: a provider returns plain SQL
    text, not the requested JSON object. Must still translate -- treated
    as bare SQL with no self-reported confidence, not a failure."""
    sql, confidence = parse_structured_response('SUM(sometable."SOMECOLUMN")')
    assert sql == 'SUM(sometable."SOMECOLUMN")'
    assert confidence is None


def test_parse_structured_response_strips_markdown_fences_and_json_language_tag():
    sql, confidence = parse_structured_response('```json\n{"sql": "SUM(x)", "confidence": 0.6}\n```')
    assert sql == "SUM(x)"
    assert confidence == 0.6


def test_parse_structured_response_empty_text_is_none_none():
    sql, confidence = parse_structured_response("")
    assert sql is None
    assert confidence is None


def test_parse_batch_payload_accepts_the_new_nested_sql_confidence_shape():
    text = '{"m0": {"sql": "SUM(x)", "confidence": 0.9}, "m1": {"sql": "AVG(y)", "confidence": 0.4}}'
    result = parse_batch_payload(text)
    assert result == {
        "m0": {"sql": "SUM(x)", "confidence": 0.9},
        "m1": {"sql": "AVG(y)", "confidence": 0.4},
    }


def test_parse_batch_payload_still_accepts_bare_string_values_for_back_compat():
    """A provider that ignores the per-key {"sql":..., "confidence":...}
    contract and returns bare SQL strings for the whole batch must still
    be tolerated -- same posture as the single-item path."""
    result = parse_batch_payload('{"m0": "SUM(x)", "m1": "AVG(y)"}')
    assert result == {"m0": "SUM(x)", "m1": "AVG(y)"}
