from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from semabridge.repository.orm import Base, ParsedSemanticPayload, Project, Run, SourceArtifact
from semabridge.repository.semantic_payload_repository import backfill_parsed_semantic_payloads
from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel


def _build_legacy_sml_payload() -> dict:
    model = SMLModel(
        unique_name="orders_model",
        version="2.1",
        datasets=[
            SMLDataset(
                unique_name="orders",
                columns=[
                    SMLColumn(unique_name="order_id", data_type="integer", source_type="INT", is_key=True),
                    SMLColumn(unique_name="amount", data_type="decimal", source_type="NUMBER"),
                ],
                is_fact=True,
            )
        ],
        metrics=[
            SMLMetric(unique_name="total_amount", dataset="orders", source_column="amount", aggregation="sum")
        ],
    )
    return model.model_dump(mode="json")


def test_backfill_parsed_semantic_payloads_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        session.add(Project(project_id="project-1", name="Project One"))
        session.add(
            Run(
                run_id="run-1",
                project_id="project-1",
                started_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
                status="running",
            )
        )
        session.add(
            SourceArtifact(
                artifact_id="artifact-1",
                run_id="run-1",
                source_type="sml",
                content_json=json.dumps(_build_legacy_sml_payload()),
                created_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
            )
        )
        session.commit()

        first = backfill_parsed_semantic_payloads(session, source_types=["sml"])
        assert first == 1

        rows = session.query(ParsedSemanticPayload).all()
        assert len(rows) == 1
        assert rows[0].model_name == "orders_model"
        assert rows[0].payload["model_name"] == "orders_model"
        assert len(rows[0].payload["datasets"]) == 1

        second = backfill_parsed_semantic_payloads(session, source_types=["sml"])
        assert second == 1
        assert session.query(ParsedSemanticPayload).count() == 1
    finally:
        session.close()
