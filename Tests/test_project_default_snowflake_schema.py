"""Regression tests for the per-project default Snowflake schema.

Context: every project used to land in one shared physical schema
(SEMABRIDGE_WORKSPACE) because nothing ever gave `schema_name` a
project-specific default -- see incident_shared_schema_cross_project_
contamination in project memory. `_apply_default_snowflake_schema` closes
that gap for brand-new projects only; `_normalize_project_config_yaml`
(used for every view/save of an EXISTING project's config) must never
apply it, or an already-deployed project's target schema would silently
move out from under it.
"""
from __future__ import annotations

import yaml

from semabridge.api.services import project_projects_impl as ppi


def test_blank_schema_gets_a_per_project_default():
    yaml_text = "\n".join([
        "project_name: Demo",
        "source:",
        "  type: fabric",
        "targets:",
        "  - type: snowflake",
        "    database: SEMABRIDGE",
    ])

    normalized = ppi._normalize_new_project_config_yaml("proj-demo", yaml_text, "Demo")
    parsed = yaml.safe_load(normalized)

    assert parsed["targets"][0]["schema"] == "SEMABRIDGE_WORKSPACE_PROJ_DEMO"


def test_legacy_shared_schema_value_is_overridden():
    yaml_text = "\n".join([
        "project_name: Demo",
        "source:",
        "  type: fabric",
        "targets:",
        "  - type: snowflake",
        "    database: SEMABRIDGE",
        "    schema: SEMABRIDGE_WORKSPACE",
    ])

    normalized = ppi._normalize_new_project_config_yaml("proj-multi-test20", yaml_text, "Demo")
    parsed = yaml.safe_load(normalized)

    assert parsed["targets"][0]["schema"] == "SEMABRIDGE_WORKSPACE_PROJ_MULTI_TEST20"


def test_deliberately_chosen_schema_is_left_alone():
    yaml_text = "\n".join([
        "project_name: Demo",
        "source:",
        "  type: fabric",
        "targets:",
        "  - type: snowflake",
        "    database: SEMABRIDGE",
        "    schema: PROD_ANALYTICS",
    ])

    normalized = ppi._normalize_new_project_config_yaml("proj-demo", yaml_text, "Demo")
    parsed = yaml.safe_load(normalized)

    assert parsed["targets"][0]["schema"] == "PROD_ANALYTICS"


def test_non_snowflake_targets_are_untouched():
    yaml_text = "\n".join([
        "project_name: Demo",
        "source:",
        "  type: fabric",
        "targets:",
        "  - type: fabric",
        "    workspace: SomeWorkspace",
    ])

    normalized = ppi._normalize_new_project_config_yaml("proj-demo", yaml_text, "Demo")
    parsed = yaml.safe_load(normalized)

    assert "schema" not in parsed["targets"][0]


def test_existing_project_config_normalization_never_touches_schema():
    """_normalize_project_config_yaml (view/save of an EXISTING project's
    config) must never apply the new-project schema default -- that would
    silently migrate an already-deployed project's target schema."""
    yaml_text = "\n".join([
        "project_name: Demo",
        "source:",
        "  type: fabric",
        "targets:",
        "  - type: snowflake",
        "    database: SEMABRIDGE",
        "    schema: SEMABRIDGE_WORKSPACE",
    ])

    normalized = ppi._normalize_project_config_yaml("proj-existing", yaml_text, "Demo")
    parsed = yaml.safe_load(normalized)

    assert parsed["targets"][0]["schema"] == "SEMABRIDGE_WORKSPACE"


def test_default_schema_helper_sanitizes_project_id():
    assert ppi._default_snowflake_schema_for_project("proj-pbix_multi_test20") == (
        "SEMABRIDGE_WORKSPACE_PROJ_PBIX_MULTI_TEST20"
    )
    assert ppi._default_snowflake_schema_for_project("") == "SEMABRIDGE_WORKSPACE"
