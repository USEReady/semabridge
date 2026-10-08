"""D5 test: Verify that mapping overrides saved via YAML and mapping store
for the same (model, source_path) identity are merged and applied exactly once,
scoped to their model and completely isolated from sibling models.
"""
from __future__ import annotations

import yaml
import pytest

from semabridge.api.services.mapping_service import (
    _compat_apply_manual_mapping_overrides_to_cfg,
    _compat_parse_project_cfg_dict,
)
from semabridge.api.services.sync_execution_service import (
    _config_with_model_scoped_overrides,
)
import semabridge.api.services.project_shared as project_shared


@pytest.fixture(autouse=True)
def _clean_mappings():
    old_mappings = dict(project_shared._compat_mappings)
    project_shared._compat_mappings.clear()
    yield
    project_shared._compat_mappings.clear()
    project_shared._compat_mappings.update(old_mappings)


def test_d5_yaml_and_store_override_merged_once_and_isolated():
    project_id = "proj_d5_test"

    # 1. Base project YAML with an initial override for ModelAlpha
    initial_cfg = {
        "project_name": project_id,
        "source": {
            "mode": "multiple",
            "type": "pbix",
            "models": ["models/ModelAlpha.pbix", "models/ModelBeta.pbix"],
        },
        "mappings_overrides": [
            {
                "model": "ModelAlpha",
                "source_path": "metrics.MetricOne",
                "target_name": "METRIC_ONE_INITIAL",
                "status": "manual",
            }
        ],
    }
    yaml_text = yaml.safe_dump(initial_cfg, sort_keys=False)

    # 2. Add an edit to the mapping store for the same (ModelAlpha, metrics.MetricOne),
    # plus an edit for ModelBeta
    project_shared._compat_mappings["m1"] = {
        "id": "m1",
        "project_id": project_id,
        "model": "ModelAlpha",
        "source_path": "metrics.MetricOne",
        "source_name": "MetricOne",
        "target_name": "METRIC_ONE_UPDATED",
        "is_user_edited": True,
        "status": "manual",
    }
    project_shared._compat_mappings["m2"] = {
        "id": "m2",
        "project_id": project_id,
        "model": "ModelBeta",
        "source_path": "metrics.MetricTwo",
        "source_name": "MetricTwo",
        "target_name": "METRIC_TWO_BETA",
        "is_user_edited": True,
        "status": "manual",
    }

    # 3. Apply manual mapping overrides to config YAML
    merged_yaml = _compat_apply_manual_mapping_overrides_to_cfg(yaml_text, project_id)
    parsed = _compat_parse_project_cfg_dict(merged_yaml)

    overrides = parsed.get("mappings_overrides", [])
    assert len(overrides) == 2, f"Expected exactly 2 merged overrides, got {len(overrides)}"

    # Check ModelAlpha's override: appears exactly once, store update won over YAML
    alpha_overrides = [
        o for o in overrides
        if o.get("model") == "ModelAlpha" and o.get("source_path") == "metrics.MetricOne"
    ]
    assert len(alpha_overrides) == 1
    assert alpha_overrides[0]["target_name"] == "METRIC_ONE_UPDATED"

    # Check ModelBeta's override
    beta_overrides = [
        o for o in overrides
        if o.get("model") == "ModelBeta" and o.get("source_path") == "metrics.MetricTwo"
    ]
    assert len(beta_overrides) == 1
    assert beta_overrides[0]["target_name"] == "METRIC_TWO_BETA"

    # 4. Test execution-time scoping for ModelAlpha job
    job_alpha = {
        "model_label": "ModelAlpha",
        "pbix_path": "models/ModelAlpha.pbix",
        "source_ref": "models/ModelAlpha.pbix",
    }
    cfg_alpha = _config_with_model_scoped_overrides(parsed, job_alpha)
    alpha_job_overrides = cfg_alpha.get("mappings_overrides", [])
    assert len(alpha_job_overrides) == 1
    assert alpha_job_overrides[0]["model"] == "ModelAlpha"
    assert alpha_job_overrides[0]["target_name"] == "METRIC_ONE_UPDATED"

    # 5. Test execution-time scoping for ModelBeta job
    job_beta = {
        "model_label": "ModelBeta",
        "pbix_path": "models/ModelBeta.pbix",
        "source_ref": "models/ModelBeta.pbix",
    }
    cfg_beta = _config_with_model_scoped_overrides(parsed, job_beta)
    beta_job_overrides = cfg_beta.get("mappings_overrides", [])
    assert len(beta_job_overrides) == 1
    assert beta_job_overrides[0]["model"] == "ModelBeta"
    assert beta_job_overrides[0]["target_name"] == "METRIC_TWO_BETA"

    # 6. Another unrelated model gets none of these
    job_gamma = {
        "model_label": "ModelGamma",
        "pbix_path": "models/ModelGamma.pbix",
        "source_ref": "models/ModelGamma.pbix",
    }
    cfg_gamma = _config_with_model_scoped_overrides(parsed, job_gamma)
    gamma_job_overrides = cfg_gamma.get("mappings_overrides", [])
    assert len(gamma_job_overrides) == 0
