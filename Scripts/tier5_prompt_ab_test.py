"""Before/after A-B test for the Tier-5 prompt self-verification checklist.

WHAT THIS DOES
---------------
Re-runs the two known-bad DAX shapes from this session's real, documented
incidents (rolling-12-month DATE/INTEGER type mismatch, and a
previous-period pattern prone to LAG()/window-function misuse) through:

  (a) the OLD prompt (exactly as it existed before the verification
      checklist was added -- reconstructed via `git show HEAD:<path>`,
      so it's the real old prompt text, not a hand-typed approximation)
  (b) the NEW prompt (current repo state, with the "BEFORE YOU ANSWER,
      VERIFY" checklist)

...against a REAL LLM call for each, using whichever Tier-5 provider you
have an API key configured for. Then it runs the SAME static checks the
production pipeline already uses (type_safety_validator's
detect_date_numeric_type_mismatch, tier5/validation's
_is_scalar_metric_sql) against each of the 4 raw responses, so the verdict
is not eyeballed -- it's the same pass/fail logic Snowflake-deployment
already relies on.

CAVEAT ON THE TEST CASES: the original incidents' exact production DAX
text was never preserved verbatim in the codebase (by design -- the fixes
were kept generic, not keyed to specific metric/column names). The two
cases below are faithful reconstructions of the documented incident SHAPE
(same schema shape, same column names/types used in this repo's own
regression tests -- see Tests/test_type_safety_validator.py's
DATASET_ALIASES/COL_TYPES -- and a DAX pattern that plausibly reaches Tier
5 and requires the same judgment call), not a byte-identical replay.

HOW TO RUN
----------
1. Provider credentials, in either of two places -- no new setup needed if
   you've already configured one via the Settings page:
     (a) Settings-page-configured key (DB-stored) -- nothing to set here,
         the script resolves it the same way Tier5Service does in
         production (Tier5Config.resolve(), not .default() -- see
         _pick_adapter()'s comment for why that distinction matters).
     (b) .env / process environment, e.g.:
           PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
           bash:        export ANTHROPIC_API_KEY="sk-ant-..."
         (Any of: OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY,
         FEATHERLESS_API_KEY, ANTHROPIC_API_KEY.)
   A Settings-configured provider is tried before a .env-only one (same
   priority rule enabled_provider_order() applies in production).
2. From the semabridge repo root:
     python "<path to this file>"
3. Paste the full output back for interpretation.

This makes 4 real, cheap, single-shot LLM calls (no batch, no retries loop,
no deploy) -- not a full pipeline run.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve()
# Walk up from wherever this script is copied to find the semabridge repo
# root (the directory containing src/semabridge). Falls back to cwd.
_candidates = [Path.cwd()] + list(Path.cwd().parents)
SEMABRIDGE_ROOT = None
for c in _candidates:
    if (c / "src" / "semabridge").exists():
        SEMABRIDGE_ROOT = c
        break
if SEMABRIDGE_ROOT is None:
    raise SystemExit(
        "Run this script from inside the semabridge repo root "
        "(the directory containing src/semabridge)."
    )

sys.path.insert(0, str(SEMABRIDGE_ROOT / "src"))

from semabridge.dax_translation.types import TranslationRequest, Dialect  # noqa: E402
from semabridge.dax_translation.tier5.config import Tier5Config  # noqa: E402
from semabridge.dax_translation.tier5.service import _ADAPTER_CLASSES  # noqa: E402
from semabridge.dax_translation.tier5.prompt import (  # noqa: E402  -- NEW prompt
    build_prompt as build_prompt_new,
    build_system_message as build_system_message_new,
)
from semabridge.connectors.type_safety_validator import (  # noqa: E402
    detect_date_numeric_type_mismatch,
)
from semabridge.dax_translation.tier5.validation import _is_scalar_metric_sql  # noqa: E402


def _load_old_prompt_module():
    """Load the pre-checklist prompt.py straight from git HEAD (the
    working tree already has the checklist added), so the "old" side of
    this comparison is the real old prompt, not a re-typed guess."""
    rel_path = "src/semabridge/dax_translation/tier5/prompt.py"
    result = subprocess.run(
        ["git", "show", f"HEAD:{rel_path}"],
        cwd=str(SEMABRIDGE_ROOT),
        capture_output=True,
        check=True,
    )
    old_src = result.stdout.decode("utf-8")
    tmp_path = SEMABRIDGE_ROOT / "_prompt_old_snapshot_tmp.py"
    tmp_path.write_text(old_src, encoding="utf-8")
    try:
        spec = importlib.util.spec_from_file_location("prompt_old", tmp_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        tmp_path.unlink(missing_ok=True)
    return module


prompt_old = _load_old_prompt_module()

# --- The two known-bad-incident-shaped test cases -------------------------

DATASET_COL_LOOKUP = {
    "SalesFact": {"MONTHINDEX", "MAX_DATE", "SALESAMOUNT", "UNITS"},
}
DATASET_COL_TYPES = {
    "SalesFact": {"MONTHINDEX": "INTEGER", "MAX_DATE": "DATE", "SALESAMOUNT": "NUMBER", "UNITS": "INTEGER"},
}
DATASET_ALIASES = {"SalesFact": "SALESFACT"}  # for the type-safety checker

CASES = [
    {
        "key": "R12M_DAY_MONTH_UNIT_CONFUSION",
        "incident": "Rolling-12-month DATE/INTEGER type mismatch "
                    "(real incident: type_safety_validator.py's module docstring)",
        "request": TranslationRequest(
            dax=(
                "CALCULATE(SUM(SalesFact[SalesAmount]), "
                "FILTER(ALL(SalesFact), SalesFact[MonthIndex] > "
                "MAX(SalesFact[MonthIndex]) - 12))"
            ),
            dataset_name="SalesFact",
            table_alias="SALESFACT",
            dataset_col_lookup=DATASET_COL_LOOKUP,
            dataset_aliases=DATASET_ALIASES,
            dataset_col_types=DATASET_COL_TYPES,
            metric_name="Sales R12M",
            dialect=Dialect.SNOWFLAKE,
        ),
    },
    {
        "key": "PREVIOUS_PERIOD_LAG_MISUSE",
        "incident": "LAG()/window-function misuse for a previous-period calc "
                    "(real incident: commit f0cac52 / e8e2ae5)",
        # Revised from a first attempt that just filtered to MonthIndex-1 --
        # that reduces trivially to plain arithmetic with no real temptation
        # toward a window function either way (both old and new prompts
        # produced identical correct SQL, but the test wasn't exercising
        # the risk). A month-over-month DELTA (current minus previous) is
        # the shape that actually looks like a textbook LAG()-over-order-by
        # problem in ordinary SQL, which is what makes it a meaningful
        # stress test of the "no window functions" checklist item.
        "request": TranslationRequest(
            dax=(
                "SUM(SalesFact[SalesAmount]) - "
                "CALCULATE(SUM(SalesFact[SalesAmount]), "
                "FILTER(ALL(SalesFact), SalesFact[MonthIndex] = "
                "MAX(SalesFact[MonthIndex]) - 1))"
            ),
            dataset_name="SalesFact",
            table_alias="SALESFACT",
            dataset_col_lookup=DATASET_COL_LOOKUP,
            dataset_aliases=DATASET_ALIASES,
            dataset_col_types=DATASET_COL_TYPES,
            metric_name="Sales Previous Month",
            dialect=Dialect.SNOWFLAKE,
        ),
    },
]


def _pick_adapter():
    # Tier5Config.resolve() -- NOT .default() -- is what Tier5Service.__init__
    # actually calls in production (service.py: `self.config = config or
    # Tier5Config.resolve()`). .default() only ever reads provider_order/
    # timeouts plus a bare os.getenv(enabled_env) check per provider; it
    # never looks at the Settings-page-configured credentials at all.
    # resolve() starts from that same default() and then overlays any
    # Settings-stored key/model via
    # repository/llm_provider_credentials.py's apply_settings_overrides()
    # (reads the same DB-backed credential store the Settings UI writes
    # to, decrypting with the same Fernet mechanism used for
    # Account.encrypted_token) -- so this now sees a Settings-configured
    # key with no .env value required, exactly like a real pipeline run.
    config = Tier5Config.resolve()
    order = config.enabled_provider_order()
    if not order:
        raise SystemExit(
            "No provider is enabled -- checked both .env "
            "(OPENAI_API_KEY / GEMINI_API_KEY / GROQ_API_KEY / "
            "FEATHERLESS_API_KEY / ANTHROPIC_API_KEY) AND the Settings-page "
            "DB-stored credentials via Tier5Config.resolve(). Configure a "
            "key in either place and re-run."
        )
    name = order[0]
    settings = config.providers[name]
    source = "Settings-page (DB)" if settings.api_key else ".env"
    adapter = _ADAPTER_CLASSES[name](settings)
    if not adapter.is_available():
        raise SystemExit(f"Provider {name!r} is enabled but adapter.is_available() is False.")
    print(f"Provider {name!r} resolved from: {source}")
    return name, adapter


def _judge(sql: str) -> str:
    """Apply the SAME static checks the real pipeline uses. Returns a short
    verdict string: PASS, or the specific failure reason."""
    if sql is None:
        return "NO_SQL_RETURNED"
    type_mismatch = detect_date_numeric_type_mismatch(sql, DATASET_ALIASES, DATASET_COL_TYPES)
    if type_mismatch:
        return f"FAIL (type-safety validator): {type_mismatch}"
    if not _is_scalar_metric_sql(sql, dialect="snowflake"):
        return "FAIL (window-function/non-scalar guard): forbidden construct (OVER/PARTITION BY/SELECT/etc.) present"
    if sql.strip().upper().startswith("CAST(NULL"):
        return "DECLINED (CAST(NULL...)): model declined rather than guessing -- acceptable, not a false pass"
    return "PASS (no known static-check failure)"


def main():
    provider_name, adapter = _pick_adapter()
    print(f"Using provider: {provider_name}\n")

    rows = []
    for case in CASES:
        req = case["request"]
        print("=" * 80)
        print(f"CASE: {case['key']}")
        print(f"Real incident this reproduces: {case['incident']}")
        print(f"DAX: {req.dax}")
        print("-" * 80)

        old_prompt = prompt_old.build_prompt(req)
        old_system = prompt_old.build_system_message(req.dialect)
        new_prompt = build_prompt_new(req)
        new_system = build_system_message_new(req.dialect)

        old_result = adapter.translate(old_prompt, old_system)
        new_result = adapter.translate(new_prompt, new_system)

        old_sql = old_result.text.strip() if old_result else None
        new_sql = new_result.text.strip() if new_result else None

        old_verdict = _judge(old_sql)
        new_verdict = _judge(new_sql)

        print(f"OLD prompt -> SQL: {old_sql!r}")
        print(f"OLD verdict: {old_verdict}")
        print()
        print(f"NEW prompt -> SQL: {new_sql!r}")
        print(f"NEW verdict: {new_verdict}")
        print()

        rows.append((case["key"], old_verdict, new_verdict))

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for key, old_verdict, new_verdict in rows:
        changed = "CHANGED" if old_verdict != new_verdict else "SAME"
        print(f"- {key}: OLD=[{old_verdict}]  NEW=[{new_verdict}]  ({changed})")


if __name__ == "__main__":
    main()
