from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Protocol

import yaml

from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.converter.tmdl_to_osi import TMDLToOSIConverter
from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel


class IFabricMetadataConnector(Protocol):
    """Strategy contract for metadata format adapters."""

    source_format: str

    def build_from_sml(self, sml_payload: Dict[str, Any] | SMLModel) -> Dict[str, Any]:
        ...

    def parse_to_sml(
        self,
        source_data: Dict[str, Any],
        *,
        row_counts: Dict[str, int] | None = None,
    ) -> tuple[OSIModel, SMLModel]:
        ...


@dataclass(frozen=True)
class TmdlConnector:
    source_format: str = "TMDL"

    def build_from_sml(self, sml_payload: Dict[str, Any] | SMLModel) -> Dict[str, Any]:
        sml_model = _coerce_sml_model(sml_payload)
        return _build_tmdl_tree(sml_model)

    def parse_to_sml(
        self,
        source_data: Dict[str, Any],
        *,
        row_counts: Dict[str, int] | None = None,
    ) -> tuple[OSIModel, SMLModel]:
        # Parse either an in-memory TMDL tree or a TMDL filesystem path.
        tmdl_payload = source_data.get("tmdl")
        tmdl_path = source_data.get("tmdl_path")

        if tmdl_path:
            # Read the folder and collect the TMDL tree for downstream parsing.
            p = Path(tmdl_path)
            if not p.exists() or not p.is_dir():
                raise ValueError(f"tmdl_path does not exist or is not a directory: {tmdl_path}")

            # Collect JSON/YAML files by relative path.
            collected: Dict[str, Any] = {}
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    try:
                        text = f.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    if f.suffix.lower() in {".json"}:
                        try:
                            collected[str(f.relative_to(p))] = json.loads(text)
                        except Exception:
                            collected[str(f.relative_to(p))] = text
                    elif f.suffix.lower() in {".yml", ".yaml"}:
                        try:
                            collected[str(f.relative_to(p))] = yaml.safe_load(text)
                        except Exception:
                            collected[str(f.relative_to(p))] = text
                    else:
                        collected[str(f.relative_to(p))] = text

            tmdl_payload = collected

        if not isinstance(tmdl_payload, dict):
            raise ValueError("TMDL adapter requires either a 'tmdl' payload or 'tmdl_path' with parsable files")

        osi_model = TMDLToOSIConverter().to_osi(
            {
                "tmdl": tmdl_payload,
                "workspace_id": source_data.get("workspace_id"),
                "dataset_id": source_data.get("dataset_id"),
                "display_name": source_data.get("display_name"),
            }
        )
        sml_model = OSIToSMLConverter().from_osi(osi_model, row_counts=row_counts or {})

        return osi_model, sml_model


def canonicalize_sml_payload(sml: SMLModel) -> str:
    return json.dumps(sml.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _coerce_sml_model(sml_payload: Dict[str, Any] | SMLModel) -> SMLModel:
    if isinstance(sml_payload, SMLModel):
        return sml_payload
    return SMLModel.model_validate(sml_payload)


def _build_tmdl_tree(sml_model: SMLModel) -> Dict[str, Any]:
    tree: Dict[str, Any] = {
        "manifest.json": {
            "name": sml_model.unique_name,
            "description": sml_model.description,
            "version": sml_model.version,
        }
    }

    for dataset in sml_model.datasets:
        table_payload: Dict[str, Any] = {
            "name": dataset.unique_name,
            "label": dataset.label,
            "description": dataset.description,
            "columns": [
                {
                    "name": column.unique_name,
                    "label": column.label,
                    "dataType": column.data_type.value,
                    "description": column.description,
                    "isHidden": column.is_hidden,
                    "isKey": column.is_key,
                    "formatString": column.format_string,
                    "folder": column.folder,
                    "sourceExpression": column.source_expression,
                }
                for column in dataset.columns
            ],
            "measures": [],
        }

        related_metrics = [metric for metric in sml_model.metrics if metric.dataset == dataset.unique_name]
        for metric in related_metrics:
            table_payload["measures"].append(
                {
                    "name": metric.unique_name,
                    "label": metric.label,
                    "description": metric.description,
                    "expression": metric.expression,
                    "formatString": metric.format_string,
                    "folder": metric.folder,
                    "isHidden": metric.is_hidden,
                }
            )

        tree[f"tables/{dataset.unique_name}.json"] = table_payload

    for relationship in sml_model.relationships:
        tree.setdefault("relationships.json", {"relationships": []})["relationships"].append(
            {
                "name": relationship.unique_name,
                "fromTable": relationship.from_dataset,
                "fromColumn": relationship.from_column,
                "toTable": relationship.to_dataset,
                "toColumn": relationship.to_column,
                "cardinality": relationship.cardinality.value,
                "crossFilterDirection": relationship.cross_filter.value,
                "isActive": relationship.is_active,
            }
        )

    return tree

