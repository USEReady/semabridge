"""In-Memory and File-Backed Store for Federated Domain Ontologies.

Files: ``<storage_dir>/<id>.ontology.yaml`` where ``id`` is the domain's stable
slug (validated ``[a-z0-9][a-z0-9_-]*``); the display name lives inside the
YAML (``domain:``) and is the source of truth on reload. Project -> domain
attachments are kept in ``<storage_dir>/_project_domains.json`` keyed by id,
so a rename never detaches a domain from a project.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.ontology.federated_loader import FederatedOntologyLoader, OntologyValidationError
from semabridge.ontology.models import (
    DomainOntology,
    FederatedEnterpriseOntology,
    slugify_domain,
)

logger = logging.getLogger("semabridge.ontology.store")

_ATTACHMENTS_FILE = "_project_domains.json"
_PROJECT_SETTINGS_FILE = "_project_ontology_settings.json"
CONFLICT_RESOLUTION_MODES = ("priority", "namespace")


class DomainOntologyStore:
    """Manages active domain ontologies and syncs the embedded AI semantic search index."""

    def __init__(self, storage_dir: Optional[Path] = None, seed_demo: bool = False):
        self.storage_dir = storage_dir or Path("Config/ontologies")
        self.domains: Dict[str, DomainOntology] = {}
        self.load_errors: List[Dict[str, str]] = []
        self.search_index = SemanticSearchIndex()
        self.query_engine: Optional[NLQueryEngine] = None
        self._seed_demo = seed_demo
        self._lock = threading.RLock()
        self._ensure_storage_dir()
        self._load_seed_defaults()

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    def _ensure_storage_dir(self) -> None:
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.load_errors.append({"file": str(self.storage_dir), "error": f"Could not create storage dir: {e}"})
            logger.warning("Could not create ontology storage dir %s: %s", self.storage_dir, e)

    def _path_for_id(self, domain_id: str) -> Path:
        """Safe file path for a domain id; raises ValueError if it would escape storage_dir."""
        slug = slugify_domain(domain_id)
        if not slug or slug != domain_id:
            raise OntologyValidationError([f"Invalid domain id {domain_id!r}"])
        base = self.storage_dir.resolve()
        path = (base / f"{slug}.ontology.yaml").resolve()
        if path.parent != base:
            raise OntologyValidationError([f"Domain id {domain_id!r} resolves outside the ontology storage directory"])
        return path

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------

    def _load_seed_defaults(self) -> None:
        """Load existing ontology files from disk; failures are kept in ``load_errors``."""
        if self.storage_dir.is_dir():
            files = sorted(set(self.storage_dir.glob("*.yaml")) | set(self.storage_dir.glob("*.yml")))
            for f in files:
                try:
                    dom = FederatedOntologyLoader.load_from_file(f)
                    expected = f.name.split(".")[0]
                    if dom.id != expected and slugify_domain(expected) == expected:
                        # Legacy file named after the old name-derived scheme: keep its id.
                        dom = dom.model_copy(update={"id": expected})
                    clash = self._find(dom.domain) or self._find_by_id(dom.id)
                    if clash is not None:
                        raise OntologyValidationError(
                            [f"Domain '{dom.domain}' (id '{dom.id}') is already defined by another file"]
                        )
                    self.domains[dom.domain] = dom
                except Exception as e:
                    msg = "; ".join(e.errors) if isinstance(e, OntologyValidationError) else str(e)
                    self.load_errors.append({"file": f.name, "error": msg})
                    logger.warning("Failed to load ontology %s: %s", f, msg)

            # Federation-level reference check once everything is loaded.
            all_domains = list(self.domains.values())
            for dom in all_domains:
                errs = FederatedOntologyLoader.validate_references(dom, all_domains, require_external=True)
                for err in errs:
                    self.load_errors.append({"file": f"{dom.id}.ontology.yaml", "error": err})

        # Demo domains (Finance and Sales) are opt-in only; never injected into real stores.
        if not self.domains:
            if self._seed_demo:
                self._seed_sample_domains()
            else:
                logger.info("Ontology store is empty (no files in %s); demo seeding disabled.", self.storage_dir)

        self._refresh_index()

    def seed_demo_domains(self) -> None:
        """Explicitly load the demo Finance/Sales domains (in memory only) and re-index."""
        self._seed_sample_domains()
        self._refresh_index()

    def _seed_sample_domains(self) -> None:
        finance_yaml = """  # hardcoding-ok: demo seed domain
domain: Finance
owner: finance-governance@enterprise.com
version: 1.0.0
description: Core financial metrics, revenue recognition, and profitability concepts.
concepts:
  - name: GeneralLedger
    label: General Ledger
    concept_type: ENTITY
    description: Master general ledger transactions
    physical_table: FACT_GL_TRANSACTIONS
    synonyms: [GL, Ledger, BookOfRecord]
    business_rules:
      - "Debits must equal credits at posting close"
    data_steward: "CFO Office"
    attributes:
      - name: transaction_id
        is_primary_key: true
        data_type: VARCHAR
      - name: account_code
        data_type: VARCHAR
      - name: amount
        data_type: DECIMAL(18,2)
      - name: posting_date
        data_type: DATE
metrics:
  - name: gross_margin
    label: Gross Margin ($)
    description: Total revenue minus cost of goods sold
    sql_expression: "SUM(revenue) - SUM(cogs)"
    aggregation_type: SUM
    grain: [account_code, posting_date]
    synonyms: [GM, Margin, GrossProfit]
    business_rules:
      - "Excludes intercompany transfer eliminations"
    data_steward: "VP Financial Planning"
  - name: ebitda
    label: EBITDA
    description: Earnings before interest, taxes, depreciation, and amortization
    sql_expression: "SUM(operating_income) + SUM(depreciation) + SUM(amortization)"
    aggregation_type: SUM
    synonyms: [OperatingCashFlow, EBITDA_USD]
    data_steward: "VP Financial Planning"
relationships: []
"""
        sales_yaml = """  # hardcoding-ok: demo seed domain
domain: Sales
owner: revops-team@enterprise.com
version: 1.0.0
description: Revenue operations, customer accounts, and order transactions.
concepts:
  - name: Customer
    label: Customer Account
    concept_type: ENTITY
    description: Master enterprise customer dimension
    physical_table: DIM_CUSTOMER
    synonyms: [Client, Account, Buyer]
    data_steward: "RevOps Team"
    attributes:
      - name: customer_id
        is_primary_key: true
        data_type: VARCHAR
      - name: region
        data_type: VARCHAR
      - name: segment
        data_type: VARCHAR
metrics:
  - name: arr
    label: Annual Recurring Revenue
    description: Annualized value of active recurring subscriptions
    sql_expression: "SUM(monthly_contract_value) * 12"
    aggregation_type: SUM
    grain: [customer_id, contract_id]
    synonyms: [ARR, SubscriptionRunRate]
    data_steward: "RevOps Lead"
relationships:
  - name: REL_GL_CUSTOMER
    from_concept: GeneralLedger
    to_concept: Customer
    cardinality: N:1
    join_expression: "GeneralLedger.customer_id = Customer.customer_id"
"""
        try:
            dom_fin = FederatedOntologyLoader.parse_yaml_string(finance_yaml, "Finance")  # hardcoding-ok: demo seed domain
            dom_sales = FederatedOntologyLoader.parse_yaml_string(sales_yaml, "Sales")  # hardcoding-ok: demo seed domain
            self.domains[dom_fin.domain] = dom_fin
            self.domains[dom_sales.domain] = dom_sales
        except Exception as e:
            logger.error("Error seeding sample domains: %s", e)

    def _refresh_index(self) -> None:
        """Re-merge federated domains and re-index the AI search engine."""
        fed = self.get_federated_ontology()
        self.search_index.index_ontology(fed)
        self.query_engine = NLQueryEngine(self.search_index, fed)

    def get_federated_ontology(self) -> FederatedEnterpriseOntology:
        return FederatedOntologyLoader.merge_domains(list(self.domains.values()))

    # ------------------------------------------------------------------
    # lookup
    # ------------------------------------------------------------------

    def _find(self, name: Optional[str]) -> Optional[DomainOntology]:
        if not name:
            return None
        if name in self.domains:
            return self.domains[name]
        key = str(name).casefold()
        for dname, dom in self.domains.items():
            if dname.casefold() == key:
                return dom
        return None

    def _find_by_id(self, domain_id: Optional[str]) -> Optional[DomainOntology]:
        if not domain_id:
            return None
        for dom in self.domains.values():
            if dom.id == domain_id:
                return dom
        return None

    def get_domain(self, name_or_id: str) -> Optional[DomainOntology]:
        """Find a domain by display name (case-insensitive) or by id."""
        return self._find(name_or_id) or self._find_by_id(name_or_id)

    # ------------------------------------------------------------------
    # mutation
    # ------------------------------------------------------------------

    def validate_domain(
        self,
        domain_ontology: DomainOntology,
        original: Optional[str] = None,
        batch: Optional[List[DomainOntology]] = None,
        enforce_bi_formula_readonly: bool = False,
    ) -> List[str]:
        """Federation-level validation (references, name/id clashes, read-only BI formulas). Returns errors."""
        errors: List[str] = []
        orig = self.get_domain(original) if original else None
        if original and orig is None:
            errors.append(f"Domain '{original}' not found.")
            return errors
        same = orig or self._find(domain_ontology.domain)
        if enforce_bi_formula_readonly and same is not None:
            errors.extend(FederatedOntologyLoader.validate_bi_formula_immutability(domain_ontology, same))
        if enforce_bi_formula_readonly:
            errors.extend(
                FederatedOntologyLoader.validate_bi_formula_shadowing(
                    domain_ontology, self._project_related_domains(domain_ontology, same)
                )
            )
        # Name clash: renaming onto another existing domain.
        other_named = self._find(domain_ontology.domain)
        if other_named is not None and orig is not None and other_named is not orig:
            errors.append(f"Domain '{domain_ontology.domain}' already exists; choose another name.")
        id_owner = self._find_by_id(domain_ontology.id)
        if id_owner is not None and id_owner is not same:
            errors.append(
                f"Domain id '{domain_ontology.id}' is already used by domain '{id_owner.domain}'; "
                "set a different 'id' or rename the domain."
            )
        others = [d for d in self.domains.values() if d is not same] + [d for d in (batch or []) if d is not domain_ontology]
        errors.extend(FederatedOntologyLoader.validate_references(domain_ontology, others, require_external=True))
        return errors

    def _project_related_domains(
        self, candidate: DomainOntology, same: Optional[DomainOntology]
    ) -> List[DomainOntology]:
        """Other domains that share a project with ``candidate`` (attachment or source project)."""
        ids = {i for i in (candidate.id, getattr(same, "id", None)) if i}
        attachments = self._read_attachments()
        pids = {pid for pid, dom_ids in attachments.items() if ids & set(dom_ids)}
        src = (candidate.source_project_id or getattr(same, "source_project_id", None) or "").strip()
        if src:
            pids.add(src)
        if not pids:
            return []
        pids_cf = {p.casefold() for p in pids}
        related_ids = {i for pid in pids for i in attachments.get(pid, [])}
        out: List[DomainOntology] = []
        for dom in self.domains.values():
            if dom is same or dom is candidate:
                continue
            if dom.id in related_ids or (dom.source_project_id or "").strip().casefold() in pids_cf:
                out.append(dom)
        return out

    def add_or_update_domain(
        self,
        domain_ontology: DomainOntology,
        save_to_disk: bool = True,
        original: Optional[str] = None,
        batch: Optional[List[DomainOntology]] = None,
        enforce_bi_formula_readonly: bool = False,
    ) -> FederatedEnterpriseOntology:
        """Create, update or (with ``original``) rename a domain.

        ``original`` is the current name or id of the domain being edited. A
        rename keeps the domain's stable id (unless the YAML sets a new one), so
        project attachments survive; the old file is removed if the id changed.
        Raises :class:`OntologyValidationError` on invalid content.
        """
        with self._lock:
            orig = self.get_domain(original) if original else None
            if orig is not None and domain_ontology.id == slugify_domain(domain_ontology.domain) and orig.id:
                # The YAML did not pin an id: keep the existing stable id across a rename.
                domain_ontology = domain_ontology.model_copy(update={"id": orig.id})
            elif orig is None:
                existing = self._find(domain_ontology.domain)
                if existing is not None and existing.id and domain_ontology.id == slugify_domain(domain_ontology.domain):
                    domain_ontology = domain_ontology.model_copy(update={"id": existing.id})

            errors = self.validate_domain(
                domain_ontology,
                original=original,
                batch=batch,
                enforce_bi_formula_readonly=enforce_bi_formula_readonly,
            )
            if errors:
                raise OntologyValidationError(errors)

            new_path = self._path_for_id(domain_ontology.id) if save_to_disk else None
            replaced = orig or self._find(domain_ontology.domain)

            if save_to_disk and self.storage_dir.is_dir():
                yaml_str = FederatedOntologyLoader.dump_domain_yaml(domain_ontology)
                tmp = new_path.with_suffix(new_path.suffix + ".tmp")
                tmp.write_text(yaml_str, encoding="utf-8")
                os.replace(tmp, new_path)

            if replaced is not None:
                self.domains.pop(replaced.domain, None)
                if save_to_disk and replaced.id and replaced.id != domain_ontology.id:
                    self._remove_file(replaced.id)
                    self._rename_attachment(replaced.id, domain_ontology.id)
            self.domains[domain_ontology.domain] = domain_ontology
            self.load_errors = [e for e in self.load_errors if e.get("file") != f"{domain_ontology.id}.ontology.yaml"]

            self._refresh_index()
            return self.get_federated_ontology()

    def _remove_file(self, domain_id: str) -> None:
        try:
            path = self._path_for_id(domain_id)
        except OntologyValidationError:
            return
        if path.is_file():
            try:
                path.unlink()
            except Exception as e:
                logger.warning("Could not delete file %s: %s", path, e)

    def delete_domain(self, domain_name: str) -> bool:
        with self._lock:
            dom = self.get_domain(domain_name)
            if dom is None:
                return False
            del self.domains[dom.domain]
            if dom.id:
                self._remove_file(dom.id)
            self._refresh_index()
            return True

    # ------------------------------------------------------------------
    # project attachments
    # ------------------------------------------------------------------

    def _attachments_path(self) -> Path:
        return self.storage_dir / _ATTACHMENTS_FILE

    def _read_attachments(self) -> Dict[str, List[str]]:
        path = self._attachments_path()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): [str(x) for x in (v or [])] for k, v in data.items()}
        except Exception as e:
            logger.warning("Could not read %s: %s", path, e)
        return {}

    def _write_attachments(self, data: Dict[str, List[str]]) -> None:
        path = self._attachments_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def _rename_attachment(self, old_id: str, new_id: str) -> None:
        data = self._read_attachments()
        changed = False
        for pid, ids in data.items():
            if old_id in ids:
                data[pid] = [new_id if i == old_id else i for i in ids]
                changed = True
        if changed:
            self._write_attachments(data)

    def get_project_domain_ids(self, project_id: str) -> List[str]:
        return list(self._read_attachments().get(str(project_id), []))

    def set_project_domains(self, project_id: str, names_or_ids: List[str]) -> List[str]:
        """Attach domains (ordered: earlier = higher precedence) to a project."""
        with self._lock:
            ids: List[str] = []
            missing: List[str] = []
            for item in names_or_ids or []:
                dom = self.get_domain(str(item))
                if dom is None or not dom.id:
                    missing.append(str(item))
                elif dom.id not in ids:
                    ids.append(dom.id)
            if missing:
                raise OntologyValidationError([f"Unknown domain(s): {', '.join(missing)}"])
            data = self._read_attachments()
            if ids:
                data[str(project_id)] = ids
            else:
                data.pop(str(project_id), None)
            self._write_attachments(data)
            return ids

    def get_project_domains(self, project_id: str) -> List[DomainOntology]:
        """Attached domains in precedence order; ids that no longer exist are skipped."""
        out: List[DomainOntology] = []
        for did in self.get_project_domain_ids(project_id):
            dom = self._find_by_id(did)
            if dom is not None:
                out.append(dom)
        return out

    def describe_project_domains(self, project_id: str) -> Dict[str, Any]:
        ids = self.get_project_domain_ids(project_id)
        attached = []
        missing = []
        for did in ids:
            dom = self._find_by_id(did)
            if dom is None:
                missing.append(did)
            else:
                attached.append({"id": dom.id, "domain": dom.domain})
        return {"project_id": project_id, "domains": attached, "missing_domain_ids": missing}

    # ------------------------------------------------------------------
    # per-project ontology settings (cross-domain conflict resolution)
    # ------------------------------------------------------------------

    def _read_project_settings(self) -> Dict[str, Dict[str, Any]]:
        path = self.storage_dir / _PROJECT_SETTINGS_FILE
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
        except Exception as e:
            logger.warning("Could not read %s: %s", path, e)
        return {}

    def get_conflict_resolution(self, project_id: Optional[str]) -> Optional[str]:
        """Saved cross-domain metric conflict resolution for a project: 'priority', 'namespace' or None."""
        if not project_id:
            return None
        mode = str(self._read_project_settings().get(str(project_id), {}).get("conflict_resolution") or "")
        mode = mode.strip().lower()
        return mode if mode in CONFLICT_RESOLUTION_MODES else None

    def set_conflict_resolution(self, project_id: str, mode: Optional[str]) -> Optional[str]:
        """Persist the conflict resolution choice (None clears it)."""
        norm = str(mode or "").strip().lower() or None
        if norm is not None and norm not in CONFLICT_RESOLUTION_MODES:
            raise OntologyValidationError(
                [f"Unknown conflict resolution '{mode}'; use one of: {', '.join(CONFLICT_RESOLUTION_MODES)}."]
            )
        with self._lock:
            data = self._read_project_settings()
            entry = dict(data.get(str(project_id), {}))
            if norm is None:
                entry.pop("conflict_resolution", None)
            else:
                entry["conflict_resolution"] = norm
            if entry:
                data[str(project_id)] = entry
            else:
                data.pop(str(project_id), None)
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            path = self.storage_dir / _PROJECT_SETTINGS_FILE
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        return norm


# Global singleton instance for application use.
# Set SEMABRIDGE_SEED_DEMO_ONTOLOGY=1 to preload demo domains when no ontology files exist.
ontology_store = DomainOntologyStore(
    storage_dir=Path(os.environ["SEMABRIDGE_ONTOLOGY_DIR"]) if os.environ.get("SEMABRIDGE_ONTOLOGY_DIR") else None,
    seed_demo=os.environ.get("SEMABRIDGE_SEED_DEMO_ONTOLOGY", "").strip().lower() in ("1", "true", "yes", "on"),
)
