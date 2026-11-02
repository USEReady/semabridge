"""Federated Ontology Loader, Validator, and Merger.

Enables multi-team domain-driven ownership of semantic ontologies (e.g. finance.yaml, marketing.yaml).
Performs schema validation, cross-domain join resolution, metric conflict/collision detection,
and bidirectional conversion with OSI intermediate models.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import yaml

from semabridge.intermediate.models import (
    OSICardinality,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)
from semabridge.ontology.models import (
    CardinalityType,
    ConceptType,
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    DomainRelationship,
    FederatedEnterpriseOntology,
    OntologyCollision,
)

logger = logging.getLogger("semabridge.ontology.federated_loader")


class FederatedOntologyLoader:
    """Manages parsing, validating, merging, and serializing domain ontologies."""

    @staticmethod
    def parse_yaml_string(yaml_content: str, domain_override: Optional[str] = None) -> DomainOntology:
        """Parse raw YAML text into a validated DomainOntology object."""
        try:
            data = yaml.safe_load(yaml_content)
        except Exception as e:
            raise ValueError(f"Invalid YAML syntax: {e}") from e

        if not isinstance(data, dict):
            raise ValueError("YAML root must be a dictionary / mapping.")

        domain_name = domain_override or data.get("domain") or "DefaultDomain"
        data["domain"] = domain_name

        # Set default domain on children if omitted
        for concept in data.get("concepts", []):
            if isinstance(concept, dict) and "domain" not in concept:
                concept["domain"] = domain_name

        for metric in data.get("metrics", []):
            if isinstance(metric, dict) and "domain" not in metric:
                metric["domain"] = domain_name

        return DomainOntology(**data)

    @classmethod
    def load_from_file(cls, file_path: Union[str, Path]) -> DomainOntology:
        """Load and parse a single domain ontology YAML file."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Ontology file not found: {p}")

        content = p.read_text(encoding="utf-8")
        # If domain not specified in file, derive from filename e.g. 'finance.ontology.yaml' -> 'Finance'
        derived_domain = p.name.split(".")[0].replace("_", " ").title()
        return cls.parse_yaml_string(content, domain_override=derived_domain)

    @classmethod
    def load_from_directory(cls, dir_path: Union[str, Path]) -> FederatedEnterpriseOntology:
        """Find and load all *.ontology.yaml or *.yaml files in a directory."""
        directory = Path(dir_path)
        if not directory.is_dir():
            raise NotADirectoryError(f"Directory not found: {directory}")

        yaml_files = sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml")))
        domains: List[DomainOntology] = []
        for yf in yaml_files:
            try:
                domains.append(cls.load_from_file(yf))
            except Exception as e:
                logger.warning("Failed to load ontology file %s: %s", yf, e)

        return cls.merge_domains(domains)

    @classmethod
    def merge_domains(cls, domain_list: List[DomainOntology]) -> FederatedEnterpriseOntology:
        """Merge multiple domain ontologies, detecting collisions and unresolved references."""
        fed = FederatedEnterpriseOntology()

        # Track name occurrences for collision detection
        metric_sources: Dict[str, List[Tuple[str, DomainMetric]]] = {}
        concept_sources: Dict[str, List[Tuple[str, DomainConcept]]] = {}

        for dom in domain_list:
            fed.domains[dom.domain] = dom

            # Register Concepts
            for concept in dom.concepts:
                c_key = concept.name.casefold()
                if c_key not in concept_sources:
                    concept_sources[c_key] = []
                concept_sources[c_key].append((dom.domain, concept))

                # If first occurrence, add to merged concepts
                if c_key not in fed.concepts:
                    fed.concepts[concept.name] = concept

            # Register Metrics
            for metric in dom.metrics:
                m_key = metric.name.casefold()
                if m_key not in metric_sources:
                    metric_sources[m_key] = []
                metric_sources[m_key].append((dom.domain, metric))

                # If first occurrence, add to merged metrics
                if m_key not in fed.metrics:
                    fed.metrics[metric.name] = metric

            # Register Relationships
            for rel in dom.relationships:
                fed.relationships.append(rel)

        # 1. Check Metric Collisions
        for m_key, occurrences in metric_sources.items():
            if len(occurrences) > 1:
                domains = [occ[0] for occ in occurrences]
                metrics = [occ[1] for occ in occurrences]
                exprs = {occ[0]: occ[1].sql_expression for occ in occurrences}
                descs = {occ[0]: occ[1].description for occ in occurrences}

                # Check if formulas differ
                unique_exprs = {occ[1].sql_expression.strip().casefold() for occ in occurrences}
                if len(unique_exprs) > 1:
                    fed.collisions.append(
                        OntologyCollision(
                            item_type="metric",
                            item_name=metrics[0].name,
                            conflicting_domains=domains,
                            descriptions=descs,
                            expressions=exprs,
                            severity="ERROR",
                            message=(
                                f"Metric collision: '{metrics[0].name}' defined with conflicting "
                                f"formulas across domains: {', '.join(domains)}."
                            ),
                        )
                    )
                else:
                    fed.collisions.append(
                        OntologyCollision(
                            item_type="metric",
                            item_name=metrics[0].name,
                            conflicting_domains=domains,
                            descriptions=descs,
                            expressions=exprs,
                            severity="WARNING",
                            message=(
                                f"Shared metric: '{metrics[0].name}' declared identically in multiple "
                                f"domains ({', '.join(domains)}). Suggest consolidating ownership."
                            ),
                        )
                    )

        # 2. Check Concept Collisions
        for c_key, occurrences in concept_sources.items():
            if len(occurrences) > 1:
                domains = [occ[0] for occ in occurrences]
                concepts = [occ[1] for occ in occurrences]
                descs = {occ[0]: occ[1].description for occ in occurrences}
                fed.collisions.append(
                    OntologyCollision(
                        item_type="concept",
                        item_name=concepts[0].name,
                        conflicting_domains=domains,
                        descriptions=descs,
                        severity="WARNING",
                        message=(
                            f"Concept '{concepts[0].name}' declared across multiple domains: "
                            f"{', '.join(domains)}."
                        ),
                    )
                )

        # 3. Check for Unresolved Relationships
        known_concepts = {c.casefold() for c in fed.concepts.keys()}
        for rel in fed.relationships:
            if rel.from_concept.casefold() not in known_concepts:
                fed.unresolved_references.append(
                    f"Relationship '{rel.name or (rel.from_concept + '->' + rel.to_concept)}' "
                    f"references unknown source concept: '{rel.from_concept}'"
                )
            if rel.to_concept.casefold() not in known_concepts:
                fed.unresolved_references.append(
                    f"Relationship '{rel.name or (rel.from_concept + '->' + rel.to_concept)}' "
                    f"references unknown target concept: '{rel.to_concept}'"
                )

        return fed

    @classmethod
    def to_osi_model(cls, federated: FederatedEnterpriseOntology, model_name: str = "ENTERPRISE_ONTOLOGY") -> OSIModel:
        """Convert a Federated Enterprise Ontology into a standard SemaBridge OSIModel."""
        datasets: List[OSIDataset] = []
        metrics: List[OSIMetric] = []
        relationships: List[OSIRelationship] = []

        type_map = {
            "VARCHAR": OSIDataType.STRING,
            "STRING": OSIDataType.STRING,
            "INT": OSIDataType.INTEGER,
            "INTEGER": OSIDataType.INTEGER,
            "DECIMAL": OSIDataType.DECIMAL,
            "FLOAT": OSIDataType.FLOAT,
            "BOOLEAN": OSIDataType.BOOLEAN,
            "DATE": OSIDataType.DATE,
            "DATETIME": OSIDataType.DATETIME,
        }

        # Convert Concepts to OSIDatasets
        for concept in federated.concepts.values():
            cols: List[OSIColumn] = []
            for attr in concept.attributes:
                raw_type = attr.data_type.split("(")[0].upper()
                mapped_type = type_map.get(raw_type, OSIDataType.STRING)
                cols.append(
                    OSIColumn(
                        unique_name=attr.name,
                        label=attr.name,
                        data_type=mapped_type,
                        description=attr.description,
                        is_key=attr.is_primary_key,
                        synonyms=attr.synonyms,
                    )
                )

            datasets.append(
                OSIDataset(
                    unique_name=concept.name,
                    source_table=concept.physical_table or concept.name,
                    label=concept.label or concept.name,
                    description=concept.description,
                    columns=cols,
                )
            )

        # Convert Metrics
        first_dataset = list(federated.concepts.keys())[0] if federated.concepts else "DEFAULT_DATASET"
        for m in federated.metrics.values():
            bound_ds = m.domain or first_dataset
            metrics.append(
                OSIMetric(
                    unique_name=m.name,
                    label=m.label or m.name,
                    dataset=bound_ds,
                    expression=m.sql_expression,
                    description=m.description,
                    synonyms=m.synonyms,
                )
            )

        # Convert Relationships
        for rel in federated.relationships:
            card_map = {
                CardinalityType.ONE_TO_ONE: OSICardinality.ONE_TO_ONE,
                CardinalityType.ONE_TO_MANY: OSICardinality.ONE_TO_MANY,
                CardinalityType.MANY_TO_ONE: OSICardinality.MANY_TO_ONE,
                CardinalityType.MANY_TO_MANY: OSICardinality.MANY_TO_MANY,
            }
            from_c = rel.from_concept.upper()
            to_c = rel.to_concept.upper()
            rel_name = f"REL_{from_c}_ID__{to_c}_ID"
            relationships.append(
                OSIRelationship(
                    unique_name=rel_name,
                    from_dataset=rel.from_concept,
                    to_dataset=rel.to_concept,
                    from_columns=["ID"],
                    to_columns=["ID"],
                    cardinality=card_map.get(rel.cardinality, OSICardinality.MANY_TO_ONE),
                )
            )

        return OSIModel(
            unique_name=model_name,
            label=f"{model_name.replace('_', ' ').title()}",
            description="Federated Enterprise Ontology compiled to OSI Intermediate Model",
            datasets=datasets,
            metrics=metrics,
            relationships=relationships,
        )

    @staticmethod
    def dump_domain_yaml(domain_ontology: DomainOntology) -> str:
        """Serialize a DomainOntology to clean YAML string."""
        raw_dict = domain_ontology.model_dump(exclude_none=True)
        return yaml.dump(raw_dict, sort_keys=False, indent=2)
