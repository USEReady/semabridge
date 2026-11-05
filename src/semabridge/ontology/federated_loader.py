"""Federated Ontology Loader, Validator, and Merger.

Enables multi-team domain-driven ownership of semantic ontologies (e.g. finance.yaml, marketing.yaml).
Performs schema validation, cross-domain join resolution, metric conflict/collision detection,
and bidirectional conversion with OSI intermediate models.
"""

from __future__ import annotations

import logging
import re
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

# Matches "<table>.<col> = <table>.<col>" terms inside a join expression.
_JOIN_TERM = re.compile(
    r"([A-Za-z_][\w$]*)\s*\.\s*([A-Za-z_][\w$]*)\s*=\s*([A-Za-z_][\w$]*)\s*\.\s*([A-Za-z_][\w$]*)"
)
_IDENT = re.compile(r"[A-Za-z_]\w*")

_OSI_TYPE_MAP: Dict[str, OSIDataType] = {
    "VARCHAR": OSIDataType.STRING,
    "STRING": OSIDataType.STRING,
    "TEXT": OSIDataType.STRING,
    "CHAR": OSIDataType.STRING,
    "INT": OSIDataType.INTEGER,
    "INTEGER": OSIDataType.INTEGER,
    "BIGINT": OSIDataType.INTEGER,
    "SMALLINT": OSIDataType.INTEGER,
    "DECIMAL": OSIDataType.DECIMAL,
    "NUMBER": OSIDataType.DECIMAL,
    "NUMERIC": OSIDataType.DECIMAL,
    "FLOAT": OSIDataType.FLOAT,
    "DOUBLE": OSIDataType.FLOAT,
    "REAL": OSIDataType.FLOAT,
    "BOOLEAN": OSIDataType.BOOLEAN,
    "BOOL": OSIDataType.BOOLEAN,
    "DATE": OSIDataType.DATE,
    "DATETIME": OSIDataType.DATETIME,
    "TIMESTAMP": OSIDataType.DATETIME,
    "TIMESTAMP_NTZ": OSIDataType.DATETIME,
    "TIMESTAMP_LTZ": OSIDataType.DATETIME,
    "TIMESTAMP_TZ": OSIDataType.DATETIME,
    "TIME": OSIDataType.TIME,
}


def _sanitize_name_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value).upper()


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
        """Merge multiple domain ontologies, detecting collisions and unresolved references.

        When the same concept or metric name (compared case-insensitively) is declared by
        more than one domain, the FIRST declaration wins deterministically and the
        duplicates are reported as collisions.
        """
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

                # First occurrence wins; keyed by the case-insensitive name so a later
                # duplicate can never silently replace the winner.
                if len(concept_sources[c_key]) == 1:
                    fed.concepts[concept.name] = concept

            # Register Metrics
            for metric in dom.metrics:
                m_key = metric.name.casefold()
                if m_key not in metric_sources:
                    metric_sources[m_key] = []
                metric_sources[m_key].append((dom.domain, metric))

                # First occurrence wins (see concepts above).
                if len(metric_sources[m_key]) == 1:
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

    # ------------------------------------------------------------------
    # OSI conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_metric_concept(
        metric: DomainMetric,
        concepts: Dict[str, DomainConcept],
        by_key: Dict[str, DomainConcept],
    ) -> Optional[DomainConcept]:
        """Pick the concept (OSI dataset) a metric is computed over.

        Order: explicit ``metric.concept`` -> best-matching concept in the metric's own
        domain (scored on grain / expression references) -> best match overall.
        Returns None when the ontology has no concepts at all.
        """
        if metric.concept:
            explicit = by_key.get(metric.concept.casefold())
            if explicit is not None:
                return explicit
            logger.warning(
                "Metric '%s' names concept '%s' which does not exist; inferring instead.",
                metric.name,
                metric.concept,
            )

        if not concepts:
            return None

        referenced = {g.casefold() for g in metric.grain}
        referenced.update(t.casefold() for t in _IDENT.findall(metric.sql_expression or ""))

        def score(c: DomainConcept) -> int:
            return sum(1 for a in c.attributes if a.name.casefold() in referenced)

        all_concepts = list(concepts.values())
        in_domain = [
            c for c in all_concepts
            if metric.domain and c.domain and c.domain.casefold() == metric.domain.casefold()
        ]
        pool = in_domain or all_concepts
        # max() keeps the first of equal scores, so ties resolve deterministically.
        return max(pool, key=score)

    @staticmethod
    def _parse_join_columns(
        rel: DomainRelationship,
        from_c: DomainConcept,
        to_c: DomainConcept,
    ) -> Optional[Tuple[List[str], List[str]]]:
        """Derive (from_columns, to_columns) for a relationship.

        Parses ``join_expression`` ("A.x = B.y [AND ...]"), accepting either orientation and
        either the concept name or its physical table. If there is no usable expression,
        falls back to the target's primary key only when the source has a same-named column.
        Never fabricates a join column.
        """
        from_names = {from_c.name.casefold()}
        to_names = {to_c.name.casefold()}
        if from_c.physical_table:
            from_names.add(from_c.physical_table.casefold())
        if to_c.physical_table:
            to_names.add(to_c.physical_table.casefold())

        from_cols: List[str] = []
        to_cols: List[str] = []
        for a_t, a_c, b_t, b_c in _JOIN_TERM.findall(rel.join_expression or ""):
            a_k, b_k = a_t.casefold(), b_t.casefold()
            if a_k in from_names and b_k in to_names:
                from_cols.append(a_c)
                to_cols.append(b_c)
            elif a_k in to_names and b_k in from_names:
                from_cols.append(b_c)
                to_cols.append(a_c)
        if from_cols:
            return from_cols, to_cols

        from_attrs = {a.name.casefold(): a.name for a in from_c.attributes}
        pk_pairs = [
            (from_attrs[a.name.casefold()], a.name)
            for a in to_c.attributes
            if a.is_primary_key and a.name.casefold() in from_attrs
        ]
        if pk_pairs:
            return [p[0] for p in pk_pairs], [p[1] for p in pk_pairs]
        return None

    @classmethod
    def to_osi_model(cls, federated: FederatedEnterpriseOntology, model_name: str = "ENTERPRISE_ONTOLOGY") -> OSIModel:
        """Convert a Federated Enterprise Ontology into a standard SemaBridge OSIModel."""
        datasets: List[OSIDataset] = []
        metrics: List[OSIMetric] = []
        relationships: List[OSIRelationship] = []

        # Convert Concepts to OSIDatasets
        for concept in federated.concepts.values():
            cols: List[OSIColumn] = []
            for attr in concept.attributes:
                raw_type = attr.data_type.split("(")[0].strip().upper()
                mapped_type = _OSI_TYPE_MAP.get(raw_type, OSIDataType.STRING)
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

        by_key: Dict[str, DomainConcept] = {c.name.casefold(): c for c in federated.concepts.values()}

        # Convert Metrics (bound to a real concept/dataset, never to a domain name)
        for m in federated.metrics.values():
            bound = cls._resolve_metric_concept(m, federated.concepts, by_key)
            if bound is None:
                logger.warning(
                    "Skipping metric '%s' in OSI conversion: ontology has no concepts to bind it to.",
                    m.name,
                )
                continue
            metrics.append(
                OSIMetric(
                    unique_name=m.name,
                    label=m.label or m.name,
                    dataset=bound.name,
                    expression=m.sql_expression,
                    description=m.description,
                    synonyms=m.synonyms,
                )
            )

        # Convert Relationships (columns come from the join expression)
        card_map = {
            CardinalityType.ONE_TO_ONE: OSICardinality.ONE_TO_ONE,
            CardinalityType.ONE_TO_MANY: OSICardinality.ONE_TO_MANY,
            CardinalityType.MANY_TO_ONE: OSICardinality.MANY_TO_ONE,
            CardinalityType.MANY_TO_MANY: OSICardinality.MANY_TO_MANY,
        }
        used_names: Dict[str, int] = {}
        for rel in federated.relationships:
            from_c = by_key.get(rel.from_concept.casefold())
            to_c = by_key.get(rel.to_concept.casefold())
            label = rel.name or f"{rel.from_concept}->{rel.to_concept}"
            if from_c is None or to_c is None:
                logger.warning("Skipping relationship '%s' in OSI conversion: unknown concept.", label)
                continue

            joined = cls._parse_join_columns(rel, from_c, to_c)
            if joined is None:
                logger.warning(
                    "Skipping relationship '%s' in OSI conversion: no usable join_expression "
                    "and no shared primary-key column to infer one.",
                    label,
                )
                continue
            from_cols, to_cols = joined

            base = (
                f"REL_{_sanitize_name_part(from_c.name)}_{_sanitize_name_part(from_cols[0])}"
                f"__{_sanitize_name_part(to_c.name)}_{_sanitize_name_part(to_cols[0])}"
            )
            used_names[base] = used_names.get(base, 0) + 1
            rel_name = base if used_names[base] == 1 else f"{base}_{used_names[base]}"

            relationships.append(
                OSIRelationship(
                    unique_name=rel_name,
                    from_dataset=from_c.name,
                    to_dataset=to_c.name,
                    from_columns=from_cols,
                    to_columns=to_cols,
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

    @classmethod
    def from_osi_model(
        cls,
        model: OSIModel,
        domain_name: Optional[str] = None,
        existing_ontology: Optional[DomainOntology] = None,
    ) -> DomainOntology:
        """Convert an OSIModel into a DomainOntology.

        If ``existing_ontology`` is provided, human-authored enrichments (business rules,
        data stewards, descriptions, custom synonyms, concept types, tags, owner) are preserved.
        """
        domain = domain_name or model.label or model.unique_name or "Enterprise"
        existing_concepts: Dict[str, DomainConcept] = {}
        existing_metrics: Dict[str, DomainMetric] = {}
        existing_rels: Dict[str, DomainRelationship] = {}

        if existing_ontology:
            existing_concepts = {c.name.casefold(): c for c in existing_ontology.concepts}
            existing_metrics = {m.name.casefold(): m for m in existing_ontology.metrics}
            for r in existing_ontology.relationships:
                key = f"{r.from_concept.casefold()}->{r.to_concept.casefold()}"
                existing_rels[key] = r

        # 1. Datasets -> Concepts
        osi_to_sql_type = {
            OSIDataType.STRING: "VARCHAR",
            OSIDataType.INTEGER: "INTEGER",
            OSIDataType.DECIMAL: "DECIMAL(18,2)",
            OSIDataType.FLOAT: "FLOAT",
            OSIDataType.BOOLEAN: "BOOLEAN",
            OSIDataType.DATE: "DATE",
            OSIDataType.DATETIME: "TIMESTAMP_NTZ",
            OSIDataType.TIME: "TIME",
            OSIDataType.BINARY: "BINARY",
            OSIDataType.VARIANT: "VARIANT",
            OSIDataType.UNKNOWN: "VARCHAR",
        }

        concepts: List[DomainConcept] = []
        for ds in model.datasets:
            c_key = ds.unique_name.casefold()
            exist_c = existing_concepts.get(c_key)

            existing_attrs = {a.name.casefold(): a for a in (exist_c.attributes if exist_c else [])}
            attrs: List[DomainAttribute] = []
            for col in ds.columns:
                a_key = col.unique_name.casefold()
                exist_a = existing_attrs.get(a_key)

                raw_type = osi_to_sql_type.get(col.data_type, "VARCHAR")
                synonyms = list(dict.fromkeys(
                    (col.synonyms or []) + (exist_a.synonyms if exist_a else [])
                ))

                attrs.append(
                    DomainAttribute(
                        name=col.unique_name,
                        data_type=exist_a.data_type if (exist_a and exist_a.data_type) else raw_type,
                        description=(exist_a.description if exist_a and exist_a.description else col.description),
                        is_primary_key=exist_a.is_primary_key if exist_a else col.is_key,
                        synonyms=synonyms,
                    )
                )

            # Heuristic concept_type: if marked fact or many numeric candidate metrics -> ENTITY/DIMENSION
            inferred_type = ConceptType.ENTITY if ds.is_fact else ConceptType.DIMENSION
            concept_type = exist_c.concept_type if exist_c else inferred_type

            synonyms = list(dict.fromkeys(
                (exist_c.synonyms if exist_c else [])
            ))

            concepts.append(
                DomainConcept(
                    name=ds.unique_name,
                    label=exist_c.label if (exist_c and exist_c.label) else (ds.label or ds.unique_name),
                    concept_type=concept_type,
                    domain=domain,
                    description=exist_c.description if (exist_c and exist_c.description) else ds.description,
                    synonyms=synonyms,
                    business_rules=exist_c.business_rules if exist_c else [],
                    data_steward=exist_c.data_steward if exist_c else None,
                    physical_table=ds.source_table or ds.unique_name,
                    attributes=attrs,
                    tags=exist_c.tags if exist_c else [],
                )
            )

        # 2. Metrics
        metrics: List[DomainMetric] = []
        for m in model.metrics:
            m_key = m.unique_name.casefold()
            exist_m = existing_metrics.get(m_key)

            calc_expr = m.expression
            if not calc_expr and m.source_column:
                agg_fn = (m.aggregation.value if hasattr(m.aggregation, "value") else str(m.aggregation)).upper()
                if agg_fn == "NONE":
                    calc_expr = m.source_column
                else:
                    calc_expr = f"{agg_fn}({m.source_column})"

            calc_expr = calc_expr or "COUNT(*)"
            agg_type = (m.aggregation.value if hasattr(m.aggregation, "value") else str(m.aggregation)).upper()
            if agg_type == "NONE":
                agg_type = "SUM"

            synonyms = list(dict.fromkeys(
                (m.synonyms or []) + (exist_m.synonyms if exist_m else [])
            ))

            metrics.append(
                DomainMetric(
                    name=m.unique_name,
                    label=exist_m.label if (exist_m and exist_m.label) else (m.label or m.unique_name),
                    description=exist_m.description if (exist_m and exist_m.description) else m.description,
                    sql_expression=exist_m.sql_expression if (exist_m and exist_m.sql_expression) else calc_expr,
                    aggregation_type=exist_m.aggregation_type if (exist_m and exist_m.aggregation_type) else agg_type,
                    grain=exist_m.grain if exist_m else [],
                    synonyms=synonyms,
                    business_rules=exist_m.business_rules if exist_m else [],
                    data_steward=exist_m.data_steward if exist_m else (m.business_owner or None),
                    domain=domain,
                    concept=m.dataset or (exist_m.concept if exist_m else None),
                    tags=exist_m.tags if exist_m else [],
                )
            )

        # 3. Relationships
        card_rev_map = {
            OSICardinality.ONE_TO_ONE: CardinalityType.ONE_TO_ONE,
            OSICardinality.ONE_TO_MANY: CardinalityType.ONE_TO_MANY,
            OSICardinality.MANY_TO_ONE: CardinalityType.MANY_TO_ONE,
            OSICardinality.MANY_TO_MANY: CardinalityType.MANY_TO_MANY,
        }

        relationships: List[DomainRelationship] = []
        for rel in model.relationships:
            r_key = f"{rel.from_dataset.casefold()}->{rel.to_dataset.casefold()}"
            exist_r = existing_rels.get(r_key)

            # Reconstruct join expression: From.c1 = To.c1 AND ...
            pairs = []
            for fc, tc in zip(rel.from_columns, rel.to_columns):
                pairs.append(f"{rel.from_dataset}.{fc} = {rel.to_dataset}.{tc}")
            join_expr = " AND ".join(pairs) if pairs else None

            card = card_rev_map.get(rel.cardinality, CardinalityType.MANY_TO_ONE)

            relationships.append(
                DomainRelationship(
                    name=exist_r.name if (exist_r and exist_r.name) else rel.unique_name,
                    from_concept=rel.from_dataset,
                    to_concept=rel.to_dataset,
                    cardinality=exist_r.cardinality if exist_r else card,
                    join_expression=exist_r.join_expression if (exist_r and exist_r.join_expression) else join_expr,
                    predicate_label=exist_r.predicate_label if exist_r else None,
                )
            )

        return DomainOntology(
            domain=domain,
            owner=existing_ontology.owner if existing_ontology else None,
            version=existing_ontology.version if existing_ontology else "1.0.0",
            description=existing_ontology.description if existing_ontology else model.description,
            concepts=concepts,
            metrics=metrics,
            relationships=relationships,
        )

    @staticmethod
    def dump_domain_yaml(domain_ontology: DomainOntology) -> str:
        """Serialize a DomainOntology to clean YAML string."""
        raw_dict = domain_ontology.model_dump(exclude_none=True)
        return yaml.dump(raw_dict, sort_keys=False, indent=2)
