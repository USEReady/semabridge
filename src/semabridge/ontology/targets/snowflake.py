"""Snowflake: its deployment facts already live in the ontology YAML (parsed from
the deployed semantic-view DDL at sync -- logical_name, semantic_view_name,
deploy_reason, sql_expression). This adapter changes nothing and stores nothing:
the ontology screens keep using those fields as today. It exists so callers can
treat every target the same way."""

from __future__ import annotations

from semabridge.ontology.targets.base import OntologyTargetAdapter, TargetFacts


class SnowflakeOntologyAdapter(OntologyTargetAdapter):
    target = "snowflake"

    def facts(self, project_id: str, model_key: str = "") -> TargetFacts:
        return TargetFacts(target=self.target, available=False,
                           notes=["Snowflake facts are part of the ontology itself (from the deployed DDL)."])
