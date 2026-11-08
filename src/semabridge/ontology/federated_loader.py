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
from pydantic import ValidationError

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


class OntologyValidationError(ValueError):
    """Raised for invalid ontology content; ``errors`` holds one message per problem."""

    def __init__(self, errors: List[str]):
        self.errors = list(errors) or ["Invalid ontology"]
        super().__init__("; ".join(self.errors))


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently keeping the last."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hashable = key in seen
        except TypeError:
            hashable = False
        if hashable:
            mark = key_node.start_mark
            raise OntologyValidationError(
                [f"Duplicate key '{key}' (line {mark.line + 1}, column {mark.column + 1})"]
            )
        try:
            seen.add(key)
        except TypeError:
            pass
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _loc(loc: Tuple[Any, ...], data: Any) -> str:
    """Human path for a pydantic error location, e.g. concepts[0] (Customer).attributes[1]."""
    parts: List[str] = []
    node = data
    for item in loc:
        if isinstance(item, int):
            name = ""
            try:
                node = node[item]
                if isinstance(node, dict) and node.get("name"):
                    name = f" ({node.get('name')})"
            except Exception:
                node = None
            parts[-1:] = [f"{parts[-1] if parts else ''}[{item}]{name}"]
        else:
            try:
                node = node.get(item) if isinstance(node, dict) else None
            except Exception:
                node = None
            parts.append(str(item))
    return ".".join(parts) or "root"


def format_validation_error(exc: ValidationError, data: Any = None) -> List[str]:
    """Turn a pydantic ValidationError into clear one-line messages naming the key."""
    messages: List[str] = []
    for err in exc.errors():
        loc = tuple(err.get("loc") or ())
        etype = err.get("type", "")
        if etype == "extra_forbidden":
            key = loc[-1] if loc else "?"
            where = _loc(loc[:-1], data) if len(loc) > 1 else "top level"
            messages.append(f"Unknown key '{key}' at {where}")
        elif etype == "missing":
            key = loc[-1] if loc else "?"
            where = _loc(loc[:-1], data) if len(loc) > 1 else "top level"
            messages.append(f"Missing required key '{key}' at {where}")
        else:
            msg = str(err.get("msg", "invalid value"))
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            messages.append(f"{_loc(loc, data)}: {msg}" if loc else msg)
    return messages


class FederatedOntologyLoader:
    """Manages parsing, validating, merging, and serializing domain ontologies."""

    @staticmethod
    def parse_yaml_string(yaml_content: str, domain_override: Optional[str] = None) -> DomainOntology:
        """Parse raw YAML text into a validated DomainOntology object.

        The YAML ``domain`` key is the source of truth for the domain name.
        ``domain_override`` is only a fallback for programmatic callers whose
        YAML has no ``domain`` key; it never renames a domain. Raises
        :class:`OntologyValidationError` (a ValueError) with one message per problem.
        """
        try:
            data = yaml.load(yaml_content, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
        except OntologyValidationError:
            raise
        except Exception as e:
            raise OntologyValidationError([f"Invalid YAML syntax: {e}"]) from e

        if not isinstance(data, dict):
            raise OntologyValidationError(["YAML root must be a dictionary / mapping."])

        if not str(data.get("domain") or "").strip():
            if domain_override and str(domain_override).strip():
                data["domain"] = str(domain_override).strip()
            else:
                raise OntologyValidationError(["Missing required key 'domain' at top level"])
        domain_name = str(data["domain"]).strip()

        for section in ("concepts", "metrics", "relationships"):
            if section in data and data[section] is None:
                data[section] = []
            if section in data and not isinstance(data[section], list):
                raise OntologyValidationError([f"'{section}' must be a list"])

        # Set default domain on children if omitted
        for concept in data.get("concepts", []):
            if isinstance(concept, dict) and "domain" not in concept:
                concept["domain"] = domain_name
        for metric in data.get("metrics", []):
            if isinstance(metric, dict) and "domain" not in metric:
                metric["domain"] = domain_name

        try:
            dom = DomainOntology(**data)
        except ValidationError as e:
            raise OntologyValidationError(format_validation_error(e, data)) from e

        errors = FederatedOntologyLoader.validate_references(dom, [], require_external=False)
        if errors:
            raise OntologyValidationError(errors)
        return dom

    @staticmethod
    def validate_references(
        dom: DomainOntology,
        other_domains: List[DomainOntology],
        require_external: bool = True,
    ) -> List[str]:
        """Return one error per reference to an undefined concept / attribute.

        Rules:
        * a metric's ``concept`` must name a concept declared in the SAME domain
          (a domain owns the metrics computed over its concepts);
        * a metric's ``grain`` entries must be attributes of that concept (when
          the concept declares attributes);
        * relationship endpoints may cross domains, so they are checked against
          this domain plus ``other_domains`` -- only when ``require_external`` is
          True (federation-level check done by the store), since a single file
          cannot know the rest of the mesh.
        """
        errors: List[str] = []
        local = {c.name.casefold(): c for c in dom.concepts}
        for m in dom.metrics:
            if not m.concept:
                continue
            concept = local.get(m.concept.casefold())
            if concept is None:
                errors.append(
                    f"Metric '{m.name}' references undefined concept '{m.concept}' "
                    f"(metric concepts must be declared in domain '{dom.domain}')"
                )
                continue
            attr_names = {a.name.casefold() for a in concept.attributes}
            for g in m.grain:
                if attr_names and g.casefold() not in attr_names:
                    errors.append(
                        f"Metric '{m.name}' grain '{g}' is not an attribute of concept '{concept.name}'"
                    )
        if require_external:
            known = set(local)
            for other in other_domains:
                if other is dom or (other.id and other.id == dom.id):
                    continue
                known.update(c.name.casefold() for c in other.concepts)
            for r in dom.relationships:
                label = r.name or f"{r.from_concept}->{r.to_concept}"
                for end in (r.from_concept, r.to_concept):
                    if end.casefold() not in known:
                        errors.append(f"Relationship '{label}' references undefined concept '{end}'")
        return errors

    @classmethod
    def load_from_file(cls, file_path: Union[str, Path]) -> DomainOntology:
        """Load and parse a single domain ontology YAML file.

        The domain name always comes from the file's ``domain`` key. Only legacy
        files without one fall back to a name derived from the file name.
        """
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Ontology file not found: {p}")

        content = p.read_text(encoding="utf-8")
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
                unique_exprs = {(occ[1].sql_expression or "").strip().casefold() for occ in occurrences}
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
            if not (m.sql_expression or "").strip():
                logger.info("Metric '%s' has no sql_expression (annotation-only); not compiled to OSI.", m.name)
                continue
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

    @staticmethod
    def _merge_description(existing: Any, bi_value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """Return (description, bi_description) for a BI-backed item on re-sync.

        The user's text wins. An existing description that is empty or equal to
        the previous BI baseline was never edited, so it follows the new BI text.
        Legacy items (no baseline recorded) keep their description when non-empty.
        """
        bi_value = bi_value or None
        if existing is None:
            return bi_value, bi_value
        cur = existing.description or None
        prev_bi = getattr(existing, "bi_description", None)
        if cur is None:
            return bi_value, bi_value
        if prev_bi is not None and cur == prev_bi:
            return bi_value, bi_value  # unedited BI text -> refresh
        return cur, bi_value  # user-edited (or legacy) -> keep

    @staticmethod
    def _union(*lists: Optional[List[str]]) -> List[str]:
        out: List[str] = []
        seen: set = set()
        for lst in lists:
            for v in lst or []:
                t = str(v or "").strip()
                if t and t.casefold() not in seen:
                    seen.add(t.casefold())
                    out.append(t)
        return out

    @classmethod
    def from_osi_model(
        cls,
        model: OSIModel,
        domain_name: Optional[str] = None,
        existing_ontology: Optional[DomainOntology] = None,
        report: Optional[Dict[str, List[str]]] = None,
    ) -> DomainOntology:
        """Convert an OSIModel into a DomainOntology, MERGING into ``existing_ontology``.

        Re-sync semantics (nothing a user entered is lost):
        * BI items (origin="bi") are refreshed from the model: physical table,
          data types, formula, BI description (unless the user edited it); BI
          synonyms are unioned with the user's synonyms;
        * user-authored fields (label, business rules, steward, tags, concept
          type, maps_to, synonyms, edited descriptions) are kept;
        * user-added items (origin "user"/unset and absent from BI) are kept;
        * new BI items are added;
        * BI items that disappeared from the model are kept and marked
          ``stale: true`` (listed in ``report['stale']``), never deleted.

        ``report`` (optional dict) receives lists: added, updated, stale, kept_user.
        """
        rep: Dict[str, List[str]] = report if report is not None else {}
        for k in ("added", "updated", "stale", "kept_user"):
            rep.setdefault(k, [])
        union = cls._union

        domain = (
            (existing_ontology.domain if existing_ontology else None)
            or domain_name or model.label or model.unique_name or "Enterprise"
        )
        existing_concepts: Dict[str, DomainConcept] = {}
        existing_metrics: Dict[str, DomainMetric] = {}
        existing_rels: List[DomainRelationship] = []
        if existing_ontology:
            existing_concepts = {c.name.casefold(): c for c in existing_ontology.concepts}
            existing_metrics = {m.name.casefold(): m for m in existing_ontology.metrics}
            existing_rels = list(existing_ontology.relationships)

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

        # 1. Datasets -> Concepts
        new_concepts: Dict[str, DomainConcept] = {}
        for ds in model.datasets:
            c_key = ds.unique_name.casefold()
            if c_key in new_concepts:
                continue
            exist_c = existing_concepts.get(c_key)

            existing_attrs = {a.name.casefold(): a for a in (exist_c.attributes if exist_c else [])}
            attrs: List[DomainAttribute] = []
            bi_attr_keys: set = set()
            for col in ds.columns:
                a_key = col.unique_name.casefold()
                if a_key in bi_attr_keys:
                    continue
                bi_attr_keys.add(a_key)
                exist_a = existing_attrs.get(a_key)
                desc, bi_desc = cls._merge_description(exist_a, col.description)
                attrs.append(
                    DomainAttribute(
                        name=exist_a.name if exist_a else col.unique_name,
                        data_type=osi_to_sql_type.get(col.data_type, "VARCHAR"),
                        description=desc,
                        is_primary_key=exist_a.is_primary_key if exist_a else col.is_key,
                        synonyms=union(exist_a.synonyms if exist_a else [], col.synonyms),
                        maps_to=exist_a.maps_to if exist_a else [],
                        origin="bi",
                        stale=False,
                        bi_description=bi_desc,
                    )
                )
            for a_key, exist_a in existing_attrs.items():
                if a_key in bi_attr_keys:
                    continue
                if exist_a.origin == "bi":
                    if not exist_a.stale:
                        rep["stale"].append(f"attribute {ds.unique_name}.{exist_a.name}")
                    attrs.append(exist_a.model_copy(update={"stale": True}))
                else:
                    rep["kept_user"].append(f"attribute {ds.unique_name}.{exist_a.name}")
                    attrs.append(exist_a)

            inferred_type = ConceptType.ENTITY if ds.is_fact else ConceptType.DIMENSION
            bi_syns: List[str] = list(getattr(ds, "synonyms", None) or [])
            if ds.label and ds.label.casefold() != ds.unique_name.casefold():
                bi_syns.append(ds.label)
            desc, bi_desc = cls._merge_description(exist_c, ds.description)

            new_concepts[c_key] = DomainConcept(
                name=exist_c.name if exist_c else ds.unique_name,
                label=exist_c.label if (exist_c and exist_c.label) else (ds.label or ds.unique_name),
                concept_type=exist_c.concept_type if exist_c else inferred_type,
                domain=domain,
                description=desc,
                synonyms=union(exist_c.synonyms if exist_c else [], bi_syns),
                business_rules=exist_c.business_rules if exist_c else [],
                data_steward=exist_c.data_steward if exist_c else None,
                source_dataset=exist_c.source_dataset if exist_c else None,
                physical_table=ds.source_table or ds.unique_name,
                attributes=attrs,
                tags=exist_c.tags if exist_c else [],
                maps_to=exist_c.maps_to if exist_c else [],
                origin="bi",
                stale=False,
                bi_description=bi_desc,
            )
            rep["updated" if exist_c else "added"].append(f"concept {ds.unique_name}")

        concepts: List[DomainConcept] = []
        for c_key, exist_c in existing_concepts.items():
            if c_key in new_concepts:
                concepts.append(new_concepts.pop(c_key))
            elif exist_c.origin == "bi":
                if not exist_c.stale:
                    rep["stale"].append(f"concept {exist_c.name}")
                concepts.append(exist_c.model_copy(update={"stale": True}))
            else:
                rep["kept_user"].append(f"concept {exist_c.name}")
                concepts.append(exist_c)
        concepts.extend(new_concepts.values())

        # 2. Metrics
        new_metrics: Dict[str, DomainMetric] = {}
        for m in model.metrics:
            m_key = m.unique_name.casefold()
            if m_key in new_metrics:
                continue
            exist_m = existing_metrics.get(m_key)

            calc_expr = m.expression
            agg_fn = (m.aggregation.value if hasattr(m.aggregation, "value") else str(m.aggregation)).upper()
            if not calc_expr and m.source_column:
                calc_expr = m.source_column if agg_fn == "NONE" else f"{agg_fn}({m.source_column})"
            calc_expr = calc_expr or getattr(m, "sql_expression", None) or "COUNT(*)"
            agg_type = "SUM" if agg_fn == "NONE" else agg_fn
            user_formula = bool(exist_m and exist_m.origin != "bi" and exist_m.sql_expression)
            desc, bi_desc = cls._merge_description(exist_m, m.description)

            new_metrics[m_key] = DomainMetric(
                name=exist_m.name if exist_m else m.unique_name,
                label=exist_m.label if (exist_m and exist_m.label) else (m.label or m.unique_name),
                description=desc,
                sql_expression=exist_m.sql_expression if user_formula else calc_expr,
                aggregation_type=exist_m.aggregation_type if user_formula else agg_type,
                grain=exist_m.grain if exist_m else [],
                synonyms=union(exist_m.synonyms if exist_m else [], m.synonyms),
                business_rules=exist_m.business_rules if exist_m else [],
                data_steward=(exist_m.data_steward if (exist_m and exist_m.data_steward) else (m.business_owner or None)),
                domain=domain,
                concept=m.dataset or (exist_m.concept if exist_m else None),
                semantic_view_name=exist_m.semantic_view_name if exist_m else None,
                tags=exist_m.tags if exist_m else [],
                maps_to=exist_m.maps_to if exist_m else [],
                origin="bi",
                stale=False,
                bi_description=bi_desc,
            )
            rep["updated" if exist_m else "added"].append(f"metric {m.unique_name}")

        metrics: List[DomainMetric] = []
        for m_key, exist_m in existing_metrics.items():
            if m_key in new_metrics:
                metrics.append(new_metrics.pop(m_key))
            elif exist_m.origin == "bi":
                if not exist_m.stale:
                    rep["stale"].append(f"metric {exist_m.name}")
                metrics.append(exist_m.model_copy(update={"stale": True}))
            else:
                rep["kept_user"].append(f"metric {exist_m.name}")
                metrics.append(exist_m)
        metrics.extend(new_metrics.values())

        # A metric concept must stay resolvable inside the domain (validation rule).
        concept_names = {c.name.casefold() for c in concepts}
        for i, met in enumerate(metrics):
            if met.concept and met.concept.casefold() not in concept_names:
                rep["stale"].append(
                    f"metric {met.name}: concept '{met.concept}' no longer exists; binding removed"
                )
                metrics[i] = met.model_copy(update={"concept": None})

        # 3. Relationships (keyed by endpoints)
        card_rev_map = {
            OSICardinality.ONE_TO_ONE: CardinalityType.ONE_TO_ONE,
            OSICardinality.ONE_TO_MANY: CardinalityType.ONE_TO_MANY,
            OSICardinality.MANY_TO_ONE: CardinalityType.MANY_TO_ONE,
            OSICardinality.MANY_TO_MANY: CardinalityType.MANY_TO_MANY,
        }

        def _rkey(a: str, b: str) -> str:
            return f"{a.casefold()}->{b.casefold()}"

        first_existing: Dict[str, DomainRelationship] = {}
        for r in existing_rels:
            first_existing.setdefault(_rkey(r.from_concept, r.to_concept), r)

        new_rels: Dict[str, DomainRelationship] = {}
        for rel in model.relationships:
            r_key = _rkey(rel.from_dataset, rel.to_dataset)
            if r_key in new_rels:
                continue
            exist_r = first_existing.get(r_key)
            user_r = bool(exist_r and exist_r.origin != "bi")
            pairs = [f"{rel.from_dataset}.{fc} = {rel.to_dataset}.{tc}" for fc, tc in zip(rel.from_columns, rel.to_columns)]
            join_expr = " AND ".join(pairs) if pairs else None
            card = card_rev_map.get(rel.cardinality, CardinalityType.MANY_TO_ONE)
            new_rels[r_key] = DomainRelationship(
                name=exist_r.name if (exist_r and exist_r.name) else rel.unique_name,
                from_concept=rel.from_dataset,
                to_concept=rel.to_dataset,
                cardinality=exist_r.cardinality if user_r else card,
                join_expression=(exist_r.join_expression if (user_r and exist_r.join_expression) else join_expr),
                predicate_label=exist_r.predicate_label if exist_r else None,
                origin="bi",
            )

        relationships: List[DomainRelationship] = []
        for r in existing_rels:
            r_key = _rkey(r.from_concept, r.to_concept)
            if first_existing.get(r_key) is r and r_key in new_rels:
                relationships.append(new_rels.pop(r_key))
            elif r.origin == "bi" and first_existing.get(r_key) is r:
                if not r.stale:
                    rep["stale"].append(f"relationship {r.name or r_key}")
                relationships.append(r.model_copy(update={"stale": True}))
            else:
                relationships.append(r)
        relationships.extend(new_rels.values())

        used_rel_names: set = set()
        deduped: List[DomainRelationship] = []
        for r in relationships:
            if r.name and r.name.casefold() in used_rel_names:
                base, n = r.name, 2
                while f"{base}_{n}".casefold() in used_rel_names:
                    n += 1
                r = r.model_copy(update={"name": f"{base}_{n}"})
            if r.name:
                used_rel_names.add(r.name.casefold())
            deduped.append(r)

        return DomainOntology(
            domain=domain,
            id=existing_ontology.id if existing_ontology else None,
            owner=existing_ontology.owner if existing_ontology else None,
            version=existing_ontology.version if existing_ontology else "1.0.0",
            description=(
                existing_ontology.description
                if (existing_ontology and existing_ontology.description)
                else model.description
            ),
            concepts=concepts,
            metrics=metrics,
            relationships=deduped,
        )

    @staticmethod
    def dump_domain_yaml(domain_ontology: DomainOntology) -> str:
        """Serialize a DomainOntology to clean YAML string."""
        raw_dict = domain_ontology.model_dump(mode="json", exclude_none=True)

        def _prune(node: Any) -> Any:
            # Drop bookkeeping noise (stale: false, empty maps_to) to keep files readable.
            if isinstance(node, dict):
                return {
                    k: _prune(v) for k, v in node.items()
                    if not (k == "stale" and v is False) and not (k == "maps_to" and not v)
                }
            if isinstance(node, list):
                return [_prune(v) for v in node]
            return node

        return yaml.safe_dump(_prune(raw_dict), sort_keys=False, indent=2, allow_unicode=True)
