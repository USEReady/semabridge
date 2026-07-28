"""
Local PBIX Connector.

Air-gapped semantic model extraction from local .pbix (Power BI Desktop)
archive files. Parses the internal ZIP structure to extract:
- DataModelSchema JSON (tables, columns, measures, relationships)
- Connections.json (composite model external references)
- M Code / Power Query scripts
- Row-Level Security (RLS) roles

This connector operates entirely offline — no Microsoft cloud dependency.

Architecture:
    .pbix file → BaseConnector.extract() → OSI intermediate model
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.core.exceptions import ConnectorError, PBIXParsingError
from semabridge.core.interfaces import BaseConnector
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class LocalPBIXConnector(BaseConnector):
    """Extract semantic model metadata from a local .pbix file.

    A .pbix file is structurally a ZIP archive containing compressed
    proprietary metadata, data models, and report layout definitions.
    This connector unpacks the archive in-memory and parses the critical
    DataModelSchema JSON and Connections.json files.

    Args:
        config: Configuration dictionary. Required keys:
            - pbix_path: Path to the .pbix file on the local filesystem.

    Example:
        connector = LocalPBIXConnector({"pbix_path": "/path/to/model.pbix"})
        connector.authenticate()  # Validates file exists and is a valid ZIP
        metadata = connector.discover()
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize with path to local .pbix file.

        Args:
            config: Must contain 'pbix_path' pointing to a .pbix file.

        Raises:
            ValidationError: If pbix_path is missing from config.
        """
        self.config = config
        self.validate_config(["pbix_path"])
        self._pbix_path = Path(config["pbix_path"]).resolve()
        self._archive: Optional[zipfile.ZipFile] = None
        self._data_model_schema: Optional[Dict[str, Any]] = None
        self._connections: Optional[List[Dict[str, Any]]] = None

    def extract(self) -> Dict[str, Any]:
        """Extract raw PBIX semantic-model metadata as Fabric-like TMSL.

        Returns:
            A dictionary shaped like the Fabric model-definition payload:
            ``{"model": {...}}``.

        Raises:
            PBIXParsingError: If the PBIX file is missing, corrupt, or the
                semantic model cannot be parsed into a valid JSON/TMSL payload.
        """
        self.authenticate()
        self._open_archive()
        try:
            schema = self._extract_data_model_schema()
            if not schema:
                raise PBIXParsingError(
                    "PBIX archive does not contain a readable DataModel/DataModelSchema payload",
                    pbix_path=str(self._pbix_path),
                )

            model_payload = schema["model"] if "model" in schema and isinstance(schema["model"], dict) else schema
            raw_tmsl = {"model": model_payload}
            raw_json = json.dumps(raw_tmsl, ensure_ascii=False)
            logger.debug(
                "PBIX raw TMSL extracted from %s (%s bytes JSON)",
                self._pbix_path,
                len(raw_json.encode("utf-8")),
            )
            self._data_model_schema = model_payload
            return raw_tmsl
        finally:
            self._close_archive()

    def authenticate(self) -> None:
        """Validate that the .pbix file exists and is a valid ZIP archive.

        Since this is an offline connector, authentication is limited to
        file system checks.

        Raises:
            PBIXParsingError: If the file doesn't exist or isn't a valid ZIP.
        """
        if not self._pbix_path.exists():
            raise PBIXParsingError(
                f"PBIX file not found: {self._pbix_path}",
                pbix_path=str(self._pbix_path),
            )

        if not zipfile.is_zipfile(str(self._pbix_path)):
            raise PBIXParsingError(
                f"File is not a valid ZIP/PBIX archive: {self._pbix_path}",
                pbix_path=str(self._pbix_path),
            )

        logger.info(f"PBIX file validated: {self._pbix_path}")

    def discover(self) -> Dict[str, Any]:
        """Extract and return the full semantic model metadata.

        Parses the DataModelSchema and Connections.json from the .pbix
        archive to provide a comprehensive view of the semantic model.

        Returns:
            Dictionary containing:
            - models: List of discovered semantic model definitions.
            - tables: List of VertiPaq tables with columns and types.
            - measures: List of DAX measures and calculated columns.
            - relationships: List of model relationships with cardinality.
            - connections: External composite model references.
            - m_code: Power Query M scripts (if extractable).

        Raises:
            PBIXParsingError: If the archive structure is invalid.
        """
        self._open_archive()

        result: Dict[str, Any] = {
            "models": [],
            "tables": [],
            "measures": [],
            "relationships": [],
            "connections": [],
            "m_code": [],
            "raw_tmsl": None,
            "raw_tmsl_json": None,
            "raw_model": None,
            "presentation_metadata": [],
            "field_aliases": [],
            "metadata": {
                "source": "local_pbix",
                "file_path": str(self._pbix_path),
                "file_size_bytes": self._pbix_path.stat().st_size,
            },
        }

        # 1. Extract DataModelSchema (JSON path), then fallback to pbixray for binary DataModel
        schema: Optional[Dict[str, Any]] = None
        schema_parse_error: Optional[PBIXParsingError] = None
        try:
            schema = self._extract_data_model_schema()
        except PBIXParsingError as exc:
            schema_parse_error = exc
            logger.warning(f"JSON DataModelSchema parse failed; trying pbixray fallback: {exc}")

        if schema:
            self._data_model_schema = schema
            raw_tmsl = {"model": schema}
            result["raw_tmsl"] = raw_tmsl
            result["raw_tmsl_json"] = json.dumps(raw_tmsl, ensure_ascii=False)
            result["raw_model"] = schema
            result["tables"] = self._parse_tables(schema)
            result["measures"] = self._parse_measures(schema)
            result["relationships"] = self._parse_relationships(schema)
            result["models"] = [{
                "name": schema.get("name", self._pbix_path.stem),
                "description": schema.get("description", ""),
                "compatibility_level": schema.get("compatibilityLevel"),
                "culture": schema.get("culture", "en-US"),
            }]
            result["m_code"] = self._parse_m_expressions(schema)
        else:
            fallback_result = self._extract_with_pbixray()
            if fallback_result:
                result["tables"] = fallback_result.get("tables", [])
                result["measures"] = fallback_result.get("measures", [])
                result["relationships"] = fallback_result.get("relationships", [])
                result["m_code"] = fallback_result.get("m_code", [])
                result["models"] = fallback_result.get("models", [])
                result["metadata"]["parser"] = "pbixray"
                fallback_tmsl = self._build_tmsl_from_fallback(result)
                result["raw_tmsl"] = fallback_tmsl
                result["raw_tmsl_json"] = json.dumps(fallback_tmsl, ensure_ascii=False)
                result["raw_model"] = fallback_tmsl["model"]
            elif schema_parse_error:
                self._close_archive()
                raise schema_parse_error

        # 2. Extract Connections.json for composite model references
        connections = self._extract_connections()
        if connections:
            self._connections = connections
            result["connections"] = connections

        # 3. Extract report layout and parse presentation metadata/aliases
        layout = self._extract_report_layout()
        if layout:
            presentation_metadata = self._parse_presentation_metadata(layout)
            result["presentation_metadata"] = presentation_metadata

            aliases_by_field: Dict[Tuple[str, str, Optional[str]], List[str]] = {}

            def _add_alias(field_name: str, field_kind: str, table_name: Optional[str], title: str) -> None:
                key = (field_name, field_kind, table_name if field_kind == "column" else None)
                if key not in aliases_by_field:
                    aliases_by_field[key] = []
                if title and title.lower() != field_name.lower():
                    if title not in aliases_by_field[key]:
                        aliases_by_field[key].append(title)

            for item in presentation_metadata:
                f = item["field"]
                k = item["field_type"]
                tbl = item.get("table")
                t = item["title"]
                if k == "unknown":
                    # No adjacent Measure/Column node to type this reference.
                    # It can safely feed the measure bucket (measure names are
                    # globally unique in valid TMSL, so no table info is ever
                    # needed there). It cannot safely feed the column bucket:
                    # columns are only unique per-table, and an untyped
                    # reference never resolves a table either — attributing it
                    # to a column risks misattributing across same-named
                    # columns in different tables, so it's skipped entirely
                    # for columns rather than guessed.
                    _add_alias(f, "measure", None, t)
                else:
                    _add_alias(f, k, tbl, t)

            field_aliases = [
                {"field": f, "field_type": k, "table": tbl, "aliases": aliases}
                for (f, k, tbl), aliases in aliases_by_field.items()
            ]
            result["field_aliases"] = field_aliases

            if presentation_metadata:
                logger.info(
                    "PBIX presentation metadata extracted: %s aliases",
                    len(presentation_metadata),
                )

        self._close_archive()
        logger.info(
            f"PBIX extraction complete: {len(result['tables'])} tables, "
            f"{len(result['measures'])} measures, "
            f"{len(result['relationships'])} relationships, "
            f"{len(result['connections'])} external connections"
        )
        return result

    # -------------------------------------------------------------------
    # Internal: Archive I/O
    # -------------------------------------------------------------------

    def _open_archive(self) -> None:
        """Open the .pbix ZIP archive for reading."""
        try:
            self._archive = zipfile.ZipFile(str(self._pbix_path), "r")
        except zipfile.BadZipFile as exc:
            raise PBIXParsingError(
                f"Corrupt PBIX archive: {exc}",
                pbix_path=str(self._pbix_path),
            ) from exc

    def _close_archive(self) -> None:
        """Close the archive if open."""
        if self._archive:
            self._archive.close()
            self._archive = None

    def _read_archive_file(self, *candidate_paths: str) -> Optional[bytes]:
        """Read a file from the archive, trying multiple candidate paths.

        Args:
            candidate_paths: Possible paths within the ZIP archive.

        Returns:
            File content as bytes, or None if not found.
        """
        if not self._archive:
            return None

        archive_names = self._archive.namelist()
        for path in candidate_paths:
            if path in archive_names:
                return self._archive.read(path)

            # Case-insensitive fallback
            for name in archive_names:
                if name.lower() == path.lower():
                    return self._archive.read(name)

        return None

    # -------------------------------------------------------------------
    # Internal: DataModelSchema Extraction
    # -------------------------------------------------------------------

    def _extract_with_pbixray(self) -> Optional[Dict[str, Any]]:
        """Fallback extraction path for PBIX files that store binary DataModel.

        Returns:
            Parsed semantic model fields in connector output shape, or None when
            fallback is unavailable or parsing fails.
        """
        try:
            from pbixray import PBIXRay  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("pbixray is not installed; cannot parse binary DataModel PBIX")
            return None

        try:
            model = PBIXRay(str(self._pbix_path))
            schema_df = model.schema.copy()
            dax_measures_df = model.dax_measures.copy()
            relationships_df = model.relationships.copy()
            power_query_df = model.power_query.copy()
            metadata_df = model.metadata.copy()
        except Exception as exc:
            logger.warning(f"pbixray fallback failed: {exc}")
            return None

        tables_by_name: Dict[str, Dict[str, Any]] = {}
        for _, row in schema_df.iterrows():
            table_name = str(row.get("TableName", "") or "")
            column_name = str(row.get("ColumnName", "") or "")
            data_type = str(row.get("PandasDataType", "object") or "object")
            if not table_name or not column_name:
                continue

            table = tables_by_name.setdefault(
                table_name,
                {
                    "name": table_name,
                    "description": "",
                    "is_hidden": False,
                    "columns": [],
                    "partitions": [],
                },
            )
            table["columns"].append(
                {
                    "name": column_name,
                    "data_type": data_type,
                    "is_hidden": False,
                    "source_column": column_name,
                    "type": "data",
                }
            )

        measures: List[Dict[str, Any]] = []
        for _, row in dax_measures_df.iterrows():
            measures.append(
                {
                    "name": str(row.get("Name", "") or ""),
                    "table": str(row.get("TableName", "") or ""),
                    "expression": str(row.get("Expression", "") or ""),
                    "format_string": "",
                    "description": str(row.get("Description", "") or ""),
                    "is_hidden": False,
                    "display_folder": str(row.get("DisplayFolder", "") or ""),
                }
            )

        relationships: List[Dict[str, Any]] = []
        for _, row in relationships_df.iterrows():
            relationships.append(
                {
                    "name": "",
                    "from_table": str(row.get("FromTableName", "") or ""),
                    "from_column": str(row.get("FromColumnName", "") or ""),
                    "to_table": str(row.get("ToTableName", "") or ""),
                    "to_column": str(row.get("ToColumnName", "") or ""),
                    "cross_filtering_behavior": str(
                        row.get("CrossFilteringBehavior", "oneDirection") or "oneDirection"
                    ),
                    "is_active": bool(row.get("IsActive", True)),
                    "cardinality": str(row.get("Cardinality", "many-to-one") or "many-to-one"),
                }
            )

        m_code: List[Dict[str, Any]] = []
        for _, row in power_query_df.iterrows():
            m_code.append(
                {
                    "table": str(row.get("TableName", "") or ""),
                    "partition": "",
                    "expression": str(row.get("Expression", "") or ""),
                }
            )

        model_name = self._pbix_path.stem
        compatibility_level: Optional[int] = None
        culture = "en-US"

        for _, row in metadata_df.iterrows():
            key = str(row.get("Name", "") or "")
            value = row.get("Value")
            if key.lower() in {"name", "modelname"} and value:
                model_name = str(value)
            if key.lower() in {"compatibilitylevel", "compatibility_level"} and value:
                try:
                    compatibility_level = int(value)
                except (TypeError, ValueError):
                    compatibility_level = None
            if key.lower() in {"culture", "language"} and value:
                culture = str(value)

        logger.info(
            f"pbixray fallback extracted semantic model: {len(tables_by_name)} tables, "
            f"{len(measures)} measures"
        )

        return {
            "models": [
                {
                    "name": model_name,
                    "description": "",
                    "compatibility_level": compatibility_level,
                    "culture": culture,
                }
            ],
            "tables": list(tables_by_name.values()),
            "measures": measures,
            "relationships": relationships,
            "m_code": m_code,
        }

    def _build_tmsl_from_fallback(self, fallback_result: Dict[str, Any]) -> Dict[str, Any]:
        """Build a Fabric-like TMSL payload from pbixray fallback fields.

        This prevents downstream conversion from seeing an empty model when
        JSON DataModelSchema parsing fails but fallback extraction succeeds.
        """
        model_meta = ((fallback_result.get("models") or [{}])[0])
        model_name = model_meta.get("name") or self._pbix_path.stem

        tables_by_name: Dict[str, Dict[str, Any]] = {}
        for table in fallback_result.get("tables", []) or []:
            table_name = str(table.get("name", "") or "").strip()
            if not table_name:
                continue

            table_def: Dict[str, Any] = {
                "name": table_name,
                "columns": [],
                "partitions": [],
            }
            if table.get("description"):
                table_def["description"] = str(table.get("description", ""))
            if table.get("is_hidden"):
                table_def["isHidden"] = bool(table.get("is_hidden"))

            for col in table.get("columns", []) or []:
                col_name = str(col.get("name", "") or "").strip()
                if not col_name:
                    continue
                col_def: Dict[str, Any] = {
                    "name": col_name,
                    "dataType": self._normalize_pbixray_data_type(col.get("data_type", "string")),
                }
                if col.get("is_hidden"):
                    col_def["isHidden"] = bool(col.get("is_hidden"))
                if col.get("source_column"):
                    col_def["sourceColumn"] = str(col.get("source_column"))
                table_def["columns"].append(col_def)

            for partition in table.get("partitions", []) or []:
                p_name = str(partition.get("name", "") or "").strip() or f"{table_name}_Partition"
                source_type = str(partition.get("source_type", "") or "m").strip() or "m"
                table_def["partitions"].append({
                    "name": p_name,
                    "source": {"type": source_type},
                })

            tables_by_name[table_name] = table_def

        for measure in fallback_result.get("measures", []) or []:
            table_name = str(measure.get("table", "") or "").strip()
            measure_name = str(measure.get("name", "") or "").strip()
            if not table_name or not measure_name:
                continue

            table_def = tables_by_name.setdefault(
                table_name,
                {"name": table_name, "columns": [], "partitions": []},
            )

            measure_def: Dict[str, Any] = {
                "name": measure_name,
                "expression": measure.get("expression", "") or "",
            }
            if measure.get("description"):
                measure_def["description"] = str(measure.get("description", ""))
            if measure.get("format_string"):
                measure_def["formatString"] = str(measure.get("format_string", ""))
            if measure.get("display_folder"):
                measure_def["displayFolder"] = str(measure.get("display_folder", ""))
            if measure.get("is_hidden"):
                measure_def["isHidden"] = bool(measure.get("is_hidden"))

            table_def.setdefault("measures", []).append(measure_def)

        relationships: List[Dict[str, Any]] = []
        for rel in fallback_result.get("relationships", []) or []:
            from_table = str(rel.get("from_table", "") or "").strip()
            from_column = str(rel.get("from_column", "") or "").strip()
            to_table = str(rel.get("to_table", "") or "").strip()
            to_column = str(rel.get("to_column", "") or "").strip()
            if not (from_table and from_column and to_table and to_column):
                continue
            relationships.append(
                {
                    "name": str(rel.get("name", "") or ""),
                    "fromTable": from_table,
                    "fromColumn": from_column,
                    "toTable": to_table,
                    "toColumn": to_column,
                    "isActive": bool(rel.get("is_active", True)),
                }
            )

        model_payload: Dict[str, Any] = {
            "name": model_name,
            "tables": list(tables_by_name.values()),
            "relationships": relationships,
        }
        if model_meta.get("description"):
            model_payload["description"] = str(model_meta.get("description", ""))
        if model_meta.get("compatibility_level") is not None:
            model_payload["compatibilityLevel"] = model_meta.get("compatibility_level")
        if model_meta.get("culture"):
            model_payload["culture"] = str(model_meta.get("culture", "en-US"))

        logger.info(
            "pbixray fallback TMSL assembled: %s tables, %s relationships",
            len(model_payload["tables"]),
            len(relationships),
        )
        return {"model": model_payload}

    @staticmethod
    def _normalize_pbixray_data_type(raw_type: Any) -> str:
        """Map pbixray/Pandas dtypes into TMSL-like dataType values."""
        text = str(raw_type or "").strip().lower()
        mapping = {
            "int64": "int64",
            "int32": "int64",
            "int16": "int64",
            "int8": "int64",
            "uint64": "int64",
            "float64": "double",
            "float32": "double",
            "float": "double",
            "double": "double",
            "decimal": "decimal",
            "bool": "boolean",
            "boolean": "boolean",
            "datetime64[ns]": "dateTime",
            "datetime": "dateTime",
            "date": "dateTime",
            "string": "string",
            "object": "string",
            "category": "string",
        }
        return mapping.get(text, "string")

    @staticmethod
    def _parse_json_candidate(data: bytes) -> Optional[Dict[str, Any]]:
        """Attempt to parse bytes as JSON using robust encoding fallbacks.

        Some PBIX files store JSON metadata with UTF-16 encodings.
        Others may include binary payloads that should be skipped.
        """
        for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
            try:
                text = data.decode(encoding)
            except UnicodeDecodeError:
                continue

            normalized = text.replace("\x00", "").strip()
            if not normalized:
                continue

            try:
                parsed = json.loads(normalized)
            except json.JSONDecodeError:
                continue

            if isinstance(parsed, dict):
                return parsed

        return None

    def _extract_data_model_schema(self) -> Optional[Dict[str, Any]]:
        """Extract and parse the DataModelSchema JSON from the archive.

        The DataModelSchema is the primary metadata file containing the
        Tabular Object Model (TOM) definition of the semantic model.

        Returns:
            Parsed JSON dictionary, or None if not found.
        """
        candidates = (
            "DataModelSchema",
            "definition/model.bim",
            "model.bim",
            "DataModel",
        )

        attempted: List[str] = []
        for candidate in candidates:
            data = self._read_archive_file(candidate)
            if not data:
                continue

            attempted.append(candidate)
            schema = self._parse_json_candidate(data)
            if not schema:
                logger.warning(
                    f"Skipping non-JSON or unsupported schema payload in archive member: {candidate}"
                )
                continue

            if "model" in schema and isinstance(schema["model"], dict):
                schema = schema["model"]

            logger.info(f"DataModelSchema extracted successfully from {candidate}")
            return schema

        if not attempted:
            logger.warning("DataModelSchema not found in PBIX archive")
            return None

        raise PBIXParsingError(
            "Failed to parse semantic model JSON from PBIX archive members: "
            f"{', '.join(attempted)}",
            pbix_path=str(self._pbix_path),
        )

    # -------------------------------------------------------------------
    # Internal: Table, Measure, and Relationship Parsing
    # -------------------------------------------------------------------

    def _parse_tables(self, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse VertiPaq tables from the DataModelSchema.

        Args:
            schema: Parsed DataModelSchema dictionary.

        Returns:
            List of table definitions with columns and data types.
        """
        tables: List[Dict[str, Any]] = []

        for table in schema.get("tables", []):
            table_name = table.get("name", "")
            column_count = len(table.get("columns", []) or [])
            partition_count = len(table.get("partitions", []) or [])

            logger.info(
                "PBIX table discovered: %s (columns=%s, partitions=%s, hidden=%s)",
                table_name or "<unnamed>",
                column_count,
                partition_count,
                bool(table.get("isHidden", False)),
            )

            # Skip internal/hidden tables
            if table_name.startswith("LocalDateTable_") or table_name.startswith("DateTableTemplate_"):
                logger.info("Skipping auto-generated PBIX table: %s", table_name)
                continue

            if column_count == 0:
                logger.warning(
                    "PBIX table '%s' has no columns; preserving it as a logical table",
                    table_name or "<unnamed>",
                )
            if partition_count == 0:
                logger.warning(
                    "PBIX table '%s' has no partitions; preserving it as a logical table",
                    table_name or "<unnamed>",
                )

            columns: List[Dict[str, Any]] = []
            for col in table.get("columns", []):
                columns.append({
                    "name": col.get("name", ""),
                    "data_type": col.get("dataType", "string"),
                    "is_hidden": col.get("isHidden", False),
                    "source_column": col.get("sourceColumn", ""),
                    "type": col.get("type", "data"),
                })

            tables.append({
                "name": table_name,
                "description": table.get("description", ""),
                "is_hidden": table.get("isHidden", False),
                "columns": columns,
                "partitions": [
                    {
                        "name": p.get("name", ""),
                        "source_type": p.get("source", {}).get("type", ""),
                    }
                    for p in table.get("partitions", [])
                ],
            })

        return tables

    def _parse_measures(self, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse DAX measures and calculated columns from the schema.

        Args:
            schema: Parsed DataModelSchema dictionary.

        Returns:
            List of measure definitions with DAX expressions.
        """
        measures: List[Dict[str, Any]] = []

        for table in schema.get("tables", []):
            table_name = table.get("name", "")
            for measure in table.get("measures", []):
                measures.append({
                    "name": measure.get("name", ""),
                    "table": table_name,
                    "expression": measure.get("expression", ""),
                    "format_string": measure.get("formatString", ""),
                    "description": measure.get("description", ""),
                    "is_hidden": measure.get("isHidden", False),
                    "display_folder": measure.get("displayFolder", ""),
                })

        return measures

    def _parse_relationships(self, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse semantic relationships from the schema.

        Args:
            schema: Parsed DataModelSchema dictionary.

        Returns:
            List of relationship definitions with cardinality.
        """
        relationships: List[Dict[str, Any]] = []

        for rel in schema.get("relationships", []):
            relationships.append({
                "name": rel.get("name", ""),
                "from_table": rel.get("fromTable", ""),
                "from_column": rel.get("fromColumn", ""),
                "to_table": rel.get("toTable", ""),
                "to_column": rel.get("toColumn", ""),
                "cross_filtering_behavior": rel.get("crossFilteringBehavior", "oneDirection"),
                "is_active": rel.get("isActive", True),
                "cardinality": rel.get("fromCardinality", "many") + "-to-" + rel.get("toCardinality", "one"),
            })

        return relationships

    def _parse_m_expressions(self, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract M Code (Power Query) expressions from the model.

        Args:
            schema: Parsed DataModelSchema dictionary.

        Returns:
            List of M Code expressions with their source tables.
        """
        expressions: List[Dict[str, Any]] = []

        for table in schema.get("tables", []):
            for partition in table.get("partitions", []):
                source = partition.get("source", {})
                if source.get("type") == "m":
                    expression = source.get("expression")
                    if expression:
                        # Expression can be a string or list of strings
                        if isinstance(expression, list):
                            expression = "\n".join(expression)
                        expressions.append({
                            "table": table.get("name", ""),
                            "partition": partition.get("name", ""),
                            "expression": expression,
                        })

        return expressions

    # -------------------------------------------------------------------
    # Internal: Connections.json (Composite Model)
    # -------------------------------------------------------------------

    def _extract_connections(self) -> List[Dict[str, Any]]:
        """Extract external connection references from Connections.json.

        In composite models, a single .pbix report can establish Live
        Connections or DirectQuery links to multiple upstream semantic models.
        The Connections.json file houses these references.

        Returns:
            List of connection definitions with upstream model references.
        """
        data = self._read_archive_file(
            "Connections",
            "Connections.json",
            "connections.json",
        )

        if not data:
            return []

        try:
            text = data.decode("utf-8-sig").strip()
            connections_raw = json.loads(text)

            connections: List[Dict[str, Any]] = []

            # Connections can be a dict with keys or a list
            items = connections_raw
            if isinstance(connections_raw, dict):
                items = connections_raw.get("Connections", connections_raw.get("connections", []))

            for conn in items if isinstance(items, list) else []:
                conn_string = conn.get("ConnectionString", conn.get("connectionString", ""))
                connections.append({
                    "name": conn.get("Name", conn.get("name", "")),
                    "connection_string": conn_string,
                    "provider": conn.get("Provider", conn.get("provider", "")),
                    "type": self._classify_connection_type(conn_string),
                    "external_model_id": self._extract_model_guid(conn_string),
                })

            if connections:
                logger.info(
                    f"Found {len(connections)} external connections (composite model)"
                )
            return connections

        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(f"Failed to parse Connections.json: {exc}")
            return []

    def _extract_report_layout(self) -> Optional[Dict[str, Any]]:
        """Extract and parse the report layout JSON from the archive.

        Returns:
            Parsed JSON dictionary, or None if not found or malformed.
        """
        candidates = (
            "Report/Layout",
            "Report/layout",
            "Report",
        )
        attempted: List[str] = []
        for candidate in candidates:
            data = self._read_archive_file(candidate)
            if not data:
                continue

            attempted.append(candidate)
            layout = self._parse_json_candidate(data)
            if layout:
                logger.info("Report layout extracted successfully from %s", candidate)
                return layout
            else:
                logger.warning(
                    "Failed to parse report layout JSON from archive member: %s",
                    candidate,
                )

        if attempted:
            logger.warning("Failed to parse report layout JSON from PBIX archive")
        return None

    def _extract_field_references(self, visual: Dict[str, Any]) -> Set[Tuple[str, str, Optional[str]]]:
        """Recursively traverse a visual container's fields to extract measure
        and column field references, resolved to their source table where
        possible.

        Both DAX measures and column-bound visuals encode their binding as a
        node named after the entity kind (``Measure`` or ``Column``) with a
        ``Property`` holding the field name — confirmed against real PBIX
        Report/Layout JSON, where a column-bound Select entry has the
        identical shape as a measure-bound one:
            {"Column": {"Expression": {"SourceRef": {"Source": "p"}}, "Property": "isVanArsdel"}}
            {"Measure": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Total Units"}}

        Args:
            visual: Visual container dictionary.

        Returns:
            Set of (field_name, field_kind, table_name) tuples. field_kind is
            "measure", "column", or "unknown" (bare queryRef match with no
            adjacent Measure/Column node — confirmed empirically to never
            carry table information either). table_name is the resolved
            source table (from the query's From clause) for columns, or None
            when unresolvable. Measures are left unscoped (None) since
            measure names are globally unique in valid TMSL — no From-clause
            resolution is attempted for them.
        """
        fields: Set[Tuple[str, str, Optional[str]]] = set()
        alias_to_entity: Dict[str, str] = {}

        def _clean_field_name(name: str) -> str:
            name = name.strip()
            if "[" in name and name.endswith("]"):
                start = name.rfind("[")
                name = name[start + 1 : -1]
            elif "." in name:
                parts = name.split(".")
                if parts:
                    name = parts[-1]
            return name.strip()

        def _collect_aliases(data: Any) -> None:
            if isinstance(data, dict):
                from_clause = data.get("From")
                if isinstance(from_clause, list):
                    for source in from_clause:
                        if isinstance(source, dict):
                            alias = source.get("Name")
                            entity = source.get("Entity")
                            if isinstance(alias, str) and isinstance(entity, str) and entity.strip():
                                alias_to_entity[alias] = entity
                for val in data.values():
                    _collect_aliases(val)
            elif isinstance(data, list):
                for item in data:
                    _collect_aliases(item)

        def _resolve_table(node: Dict[str, Any]) -> Optional[str]:
            expr = node.get("Expression")
            if isinstance(expr, dict):
                source_ref = expr.get("SourceRef")
                if isinstance(source_ref, dict):
                    alias = source_ref.get("Source")
                    if isinstance(alias, str):
                        return alias_to_entity.get(alias)
            return None

        def _traverse(data: Any) -> None:
            if isinstance(data, dict):
                # Case 1: Measure -> Property, or Column -> Property (identical shape)
                for node_key, field_kind in (("Measure", "measure"), ("Column", "column")):
                    node = data.get(node_key)
                    if isinstance(node, dict):
                        prop = node.get("Property")
                        if isinstance(prop, str) and prop.strip():
                            cleaned = _clean_field_name(prop)
                            if cleaned:
                                table_name = _resolve_table(node) if field_kind == "column" else None
                                fields.add((cleaned, field_kind, table_name))

                # Case 2: queryRef (no adjacent Measure/Column node — confirmed
                # empirically to never carry a resolvable table either)
                query_ref = data.get("queryRef")
                if isinstance(query_ref, str) and query_ref.strip():
                    cleaned = _clean_field_name(query_ref)
                    if cleaned:
                        fields.add((cleaned, "unknown", None))

                for val in data.values():
                    _traverse(val)
            elif isinstance(data, list):
                for item in data:
                    _traverse(item)

        # Parse the four visual container JSON properties once, then run two
        # passes over them: first collect every From-clause alias->table
        # mapping (order-independent — doesn't rely on From preceding Select
        # in the JSON), then resolve Column/Measure references against it.
        parsed_fields: List[Any] = []
        for field_name in ("config", "query", "filters", "dataTransforms"):
            field_val = visual.get(field_name)
            if not field_val:
                continue

            parsed_val = None
            if isinstance(field_val, dict):
                parsed_val = field_val
            elif isinstance(field_val, str):
                try:
                    parsed_val = json.loads(field_val)
                except Exception as exc:
                    logger.warning(
                        "Malformed JSON in visual container '%s' field: %s",
                        field_name,
                        exc,
                    )
                    continue

            if parsed_val:
                parsed_fields.append(parsed_val)

        for parsed_val in parsed_fields:
            _collect_aliases(parsed_val)
        for parsed_val in parsed_fields:
            _traverse(parsed_val)

        return fields

    def _parse_presentation_metadata(self, layout: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse report layout sections and visual containers to extract presentation metadata.

        Args:
            layout: Parsed layout JSON dictionary.

        Returns:
            List of unique presentation metadata dictionaries.
        """
        presentation_metadata: List[Dict[str, Any]] = []
        seen_tuples = set()

        sections = layout.get("sections", [])
        if not isinstance(sections, list):
            return []

        for section in sections:
            if not isinstance(section, dict):
                continue
            page = section.get("displayName", "Unknown")
            if not page or not isinstance(page, str):
                page = "Unknown"

            visual_containers = section.get("visualContainers", [])
            if not isinstance(visual_containers, list):
                continue

            for visual in visual_containers:
                if not isinstance(visual, dict):
                    continue

                # 1. Parse config JSON (which can be a string or already a dict)
                config_val = visual.get("config")
                config_dict = {}
                if isinstance(config_val, dict):
                    config_dict = config_val
                elif isinstance(config_val, str) and config_val.strip():
                    try:
                        config_dict = json.loads(config_val)
                    except Exception as exc:
                        logger.warning("Malformed JSON in visual container config: %s", exc)
                        continue

                # 2. Extract visual type (first letter capitalized, rest unchanged)
                visual_type = "Unknown"
                single_visual = config_dict.get("singleVisual", {})
                if isinstance(single_visual, dict):
                    v_type = single_visual.get("visualType")
                    if isinstance(v_type, str) and v_type.strip():
                        v_type_strip = v_type.strip()
                        visual_type = v_type_strip[0].upper() + v_type_strip[1:] if v_type_strip else "Unknown"

                # 3. Extract title using title priority order
                # Priority:
                # 1) singleVisual.vcObjects.title
                # 2) singleVisual.objects.title
                # 3) Fallback text/value fields
                title = None
                if isinstance(single_visual, dict):
                    for title_key in ("vcObjects", "objects"):
                        key_val = single_visual.get(title_key)
                        if isinstance(key_val, dict):
                            title_section = key_val.get("title")
                            if isinstance(title_section, list) and title_section:
                                properties = title_section[0].get("properties")
                                if isinstance(properties, dict):
                                    text_prop = properties.get("text")
                                    if isinstance(text_prop, dict):
                                        expr = text_prop.get("expr")
                                        if isinstance(expr, dict) and "Literal" in expr:
                                            literal = expr["Literal"]
                                            if isinstance(literal, dict) and "Value" in literal:
                                                title = literal["Value"]
                                        if not title and "value" in text_prop:
                                            title = text_prop["value"]
                        
                        if title is not None:
                            if isinstance(title, str):
                                title = title.strip("'").strip()
                                if title:
                                    break
                            else:
                                title = None

                # Skip visual if title is missing or empty
                if not title:
                    continue

                # 4. Extract referenced fields (measures + columns) using the linear
                #    recursive traversal helper
                field_refs = self._extract_field_references(visual)
                if not field_refs:
                    continue

                # 5. Populate and deduplicate tuples
                for field_name, field_kind, table_name in field_refs:
                    # Deduplicate identical (field, kind, table, title, page, visual_type) tuples
                    tpl = (field_name, field_kind, table_name, title, page, visual_type)
                    if tpl not in seen_tuples:
                        seen_tuples.add(tpl)
                        presentation_metadata.append({
                            "field": field_name,
                            "field_type": field_kind,
                            "table": table_name,
                            "title": title,
                            "page": page,
                            "visual_type": visual_type,
                        })

        return presentation_metadata

    @staticmethod
    def _classify_connection_type(conn_string: str) -> str:
        """Classify connection type from a connection string.

        Args:
            conn_string: Raw connection string.

        Returns:
            Connection type: "live_connect", "direct_query", or "unknown".
        """
        lower = conn_string.lower()
        if "pbiservice://" in lower or "powerbi://" in lower:
            return "live_connect"
        if "provider=msolap" in lower:
            return "direct_query"
        return "unknown"

    @staticmethod
    def _extract_model_guid(conn_string: str) -> Optional[str]:
        """Extract a semantic model GUID from a Power BI connection string.

        Args:
            conn_string: Raw connection string.

        Returns:
            GUID string if found, else None.
        """
        import re
        # Look for GUID pattern in the connection string
        match = re.search(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            conn_string,
        )
        return match.group(0) if match else None
