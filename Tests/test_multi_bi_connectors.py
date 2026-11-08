"""Tableau and dbt ingestion are explicitly unsupported (never half-wired).

The previous prototypes returned OSI models with no relationships, all
Tableau columns on one table, and dbt ratio/derived metrics rewritten as
SUM(<name>). They now refuse with a clear, user-readable reason instead.
"""

import pytest
from pydantic import ValidationError

from semabridge.connectors.dbt_connector import DBT_UNSUPPORTED_REASON, DbtSemanticConnector
from semabridge.connectors.tableau_connector import (
    TABLEAU_UNSUPPORTED_REASON,
    SourceNotSupportedError,
    TableauConnector,
)
from semabridge.core.project import SourceConfig


def test_tableau_connector_refuses_with_reason(tmp_path):
    tds = tmp_path / "x.tds"
    tds.write_text("<datasource/>")
    with pytest.raises(SourceNotSupportedError) as exc:
        TableauConnector(str(tds)).extract_osi_model("M")
    assert "not supported yet" in str(exc.value)
    assert exc.value.reason == TABLEAU_UNSUPPORTED_REASON


def test_dbt_connector_refuses_with_reason():
    with pytest.raises(SourceNotSupportedError) as exc:
        DbtSemanticConnector({"semantic_models": [], "metrics": []}).extract_osi_model("M")
    assert exc.value.reason == DBT_UNSUPPORTED_REASON
    assert isinstance(exc.value, NotImplementedError)


@pytest.mark.parametrize("source_type", ["tableau", "dbt"])
def test_source_config_rejects_unsupported_bi_sources_clearly(source_type):
    with pytest.raises(ValidationError) as exc:
        SourceConfig(type=source_type)
    assert "not supported yet" in str(exc.value)
