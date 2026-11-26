"""A Databricks view recorded as catalog.schema.view is never prefixed again."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ontology.auto_bootstrap import fix_view_name, repair_view_names

BAD = 'main.semabridge."workspace.default.customerfeedback_1_metric_view"'


def test_fix_view_name():
    assert fix_view_name(BAD) == "workspace.default.customerfeedback_1_metric_view"
    assert fix_view_name("DB.SCH.SALES_SEMANTIC") == "DB.SCH.SALES_SEMANTIC"
    assert fix_view_name('DB.SCH."My View"') == 'DB.SCH."My View"'
    assert fix_view_name(None) is None


def test_databricks_formatter_undoes_double_prefix():
    assert NLQueryEngine._format_databricks_view_name(BAD) == "`workspace`.`default`.`customerfeedback_1_metric_view`"
    assert NLQueryEngine._format_databricks_view_name("cat.sch.v") == "`cat`.`sch`.`v`"


def test_deployment_info_keeps_a_qualified_databricks_view():
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import Project
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.snapshot_models import SnapshotRow

    Base.metadata.create_all(db_manager.get_engine())
    pid = f"proj-vq-{uuid.uuid4().hex[:6]}"
    with db_manager.get_session() as s:
        s.add(Project(project_id=pid, name=pid, database="main", schema="semabridge"))
        s.add(SnapshotRow(
            snapshot_id=str(uuid.uuid4()), project_id=pid, timestamp=datetime.now(timezone.utc),
            view_name="workspace.default.Store_Sales_2_metric_view", status="success",
            deployed_at=datetime.now(timezone.utc),
        ))
        s.commit()
    info = ModelRepository().get_project_deployment_info(pid)
    assert info["semantic_view"] == "workspace.default.Store_Sales_2_metric_view"
    assert (info["database"], info["schema"], info["view_name"]) == ("workspace", "default", "Store_Sales_2_metric_view")


def test_repair_rewrites_stored_domains(tmp_path, monkeypatch):
    from semabridge.ontology import store as store_mod
    from semabridge.ontology.models import DomainOntology

    store = store_mod.DomainOntologyStore(storage_dir=tmp_path)
    store.add_or_update_domain(DomainOntology.model_validate({
        "domain": "CF", "source_project_id": "proj-dbt1", "semantic_view_name": BAD,
        "metrics": [{"name": "Tenure", "sql_expression": "SUM(t)", "semantic_view_name": BAD}],
    }))
    monkeypatch.setattr(store_mod, "ontology_store", store)
    assert repair_view_names() == 1
    dom = store.get_domain("CF")
    assert dom.semantic_view_name == "workspace.default.customerfeedback_1_metric_view"
    assert dom.metrics[0].semantic_view_name == "workspace.default.customerfeedback_1_metric_view"
    assert repair_view_names() == 0
