"""Offline semantic sync pipeline with intelligent datatype inference.

This script consumes an already extracted Fabric model JSON (TMSL shape),
converts it to OSI, applies value-aware datatype inference where possible,
validates the resulting schema, and writes artifacts into output/.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.intermediate.models import OSIDataType, OSIModel


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
INTEGER_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d+|\d+\.\d*|\.\d+)$")


def _normalize_samples(raw_samples: Any) -> list[str]:
    if raw_samples is None:
        return []
    if isinstance(raw_samples, list):
        return [str(x).strip() for x in raw_samples if str(x).strip()]
    return [str(raw_samples).strip()] if str(raw_samples).strip() else []


def _infer_from_samples(samples: list[str]) -> OSIDataType | None:
    if not samples:
        return None

    if all(INTEGER_RE.match(v) for v in samples):
        return OSIDataType.INTEGER
    if all(INTEGER_RE.match(v) or FLOAT_RE.match(v) for v in samples):
        return OSIDataType.FLOAT
    if all(DATE_RE.match(v) for v in samples):
        return OSIDataType.DATE
    if all(TIMESTAMP_RE.match(v) for v in samples):
        return OSIDataType.DATETIME
    return None


def _infer_from_metadata(col_def: dict[str, Any], current: OSIDataType) -> OSIDataType:
    # Keep strong non-string types coming from TMSL unless we have better evidence.
    if current in {OSIDataType.INTEGER, OSIDataType.FLOAT, OSIDataType.DECIMAL, OSIDataType.DATE, OSIDataType.DATETIME, OSIDataType.BOOLEAN, OSIDataType.BINARY}:
        return current

    name = str(col_def.get("name", "")).lower()
    fmt = str(col_def.get("formatString", "") or "").lower()
    data_type = str(col_def.get("dataType", "") or "").lower()

    if data_type == "datetime":
        # Distinguish DATE vs TIMESTAMP from format/name hints.
        if "hh" in fmt or "ss" in fmt or "time" in name:
            return OSIDataType.DATETIME
        return OSIDataType.DATE

    if any(k in name for k in ("_date", "date_", "date")):
        return OSIDataType.DATE
    if any(k in name for k in ("timestamp", "datetime", "_ts", "time_")):
        return OSIDataType.DATETIME
    if any(k in name for k in ("amount", "price", "rate", "pct", "percent", "score", "qty", "quantity", "total", "revenue")):
        return OSIDataType.FLOAT
    if any(k in name for k in ("id", "key", "count", "num", "number", "year", "month", "day")):
        return OSIDataType.INTEGER

    return OSIDataType.STRING


def _safe_expr(dtype: OSIDataType, quoted_col: str) -> str:
    if dtype == OSIDataType.INTEGER:
        return f"TRY_TO_NUMBER({quoted_col})"
    if dtype == OSIDataType.FLOAT or dtype == OSIDataType.DECIMAL:
        return f"TRY_TO_NUMBER({quoted_col})"
    if dtype == OSIDataType.DATE:
        return f"TRY_TO_DATE({quoted_col})"
    if dtype == OSIDataType.DATETIME:
        return f"TRY_TO_TIMESTAMP({quoted_col})"
    return quoted_col


def _validate_schema(model: OSIModel) -> list[str]:
    errors: list[str] = []
    valid = {
        OSIDataType.INTEGER,
        OSIDataType.FLOAT,
        OSIDataType.DECIMAL,
        OSIDataType.DATE,
        OSIDataType.DATETIME,
        OSIDataType.STRING,
        OSIDataType.BOOLEAN,
        OSIDataType.BINARY,
    }
    for ds in model.datasets:
        for col in ds.columns:
            if col.data_type not in valid:
                errors.append(f"{ds.unique_name}.{col.unique_name}: invalid type {col.data_type}")
    return errors


def run_pipeline(input_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    tmsl_json = json.loads(input_path.read_text(encoding="utf-8"))
    model_tables = {
        t.get("name", ""): t
        for t in tmsl_json.get("model", {}).get("tables", [])
    }

    converter = TMSLToOSIConverter()
    osi_model = converter.to_osi(
        {
            "tmsl": tmsl_json,
            "workspace_id": "offline-sync",
            "dataset_id": input_path.stem,
        }
    )

    inference_changes: list[dict[str, str]] = []
    type_counts: dict[str, int] = {k: 0 for k in ["INTEGER", "FLOAT", "DATE", "TIMESTAMP", "VARCHAR"]}

    for dataset in osi_model.datasets:
        table_def = model_tables.get(dataset.unique_name, {})
        source_cols = {c.get("name", ""): c for c in table_def.get("columns", [])}

        for col in dataset.columns:
            col_def = source_cols.get(col.unique_name, {})
            before = col.data_type

            samples = _normalize_samples(
                col_def.get("sampleValues")
                or col_def.get("sample_values")
                or col_def.get("samples")
                or []
            )

            inferred = _infer_from_samples(samples)
            if inferred is None:
                inferred = _infer_from_metadata(col_def, before)

            col.data_type = inferred

            if before != inferred:
                inference_changes.append(
                    {
                        "dataset": dataset.unique_name,
                        "column": col.unique_name,
                        "from": before.value,
                        "to": inferred.value,
                    }
                )

            if inferred == OSIDataType.INTEGER:
                type_counts["INTEGER"] += 1
            elif inferred in {OSIDataType.FLOAT, OSIDataType.DECIMAL}:
                type_counts["FLOAT"] += 1
            elif inferred == OSIDataType.DATE:
                type_counts["DATE"] += 1
            elif inferred == OSIDataType.DATETIME:
                type_counts["TIMESTAMP"] += 1
            else:
                type_counts["VARCHAR"] += 1

    schema_errors = _validate_schema(osi_model)

    osi_out = output_dir / "osi_inferred.json"
    osi_out.write_text(json.dumps(osi_model.model_dump(mode="json"), indent=2), encoding="utf-8")

    sql_lines: list[str] = []
    for dataset in osi_model.datasets:
        view_name = f'{dataset.unique_name}_CASTED'
        sql_lines.append(f'CREATE OR REPLACE VIEW "{view_name}" AS')
        sql_lines.append("SELECT")
        select_parts: list[str] = []
        for col in dataset.columns:
            quoted = f'"{col.unique_name}"'
            expr = _safe_expr(col.data_type, quoted)
            select_parts.append(f"  {expr} AS {quoted}")
        sql_lines.append(",\n".join(select_parts))
        sql_lines.append(f'FROM "{dataset.source_table or dataset.unique_name}";')
        sql_lines.append("")

    safe_sql_out = output_dir / "safe_cast_views.sql"
    safe_sql_out.write_text("\n".join(sql_lines), encoding="utf-8")

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "input": str(input_path),
        "outputs": {
            "osi": str(osi_out),
            "safe_cast_sql": str(safe_sql_out),
        },
        "summary": {
            "datasets": len(osi_model.datasets),
            "metrics": len(osi_model.metrics),
            "relationships": len(osi_model.relationships),
            "type_counts": type_counts,
            "varchar_ratio": (
                type_counts["VARCHAR"]
                / max(1, sum(type_counts.values()))
            ),
            "schema_validation_passed": len(schema_errors) == 0,
        },
        "inference_changes": inference_changes,
        "schema_errors": schema_errors,
    }

    report_out = output_dir / "schema_inference_report.json"
    report_out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline full sync with datatype inference")
    parser.add_argument(
        "--input",
        default="output/debug/raw_fabric_model.json",
        help="Path to extracted Fabric model JSON",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for generated artifacts",
    )
    args = parser.parse_args()

    report = run_pipeline(Path(args.input), Path(args.output_dir))
    if not report["summary"]["schema_validation_passed"]:
        print("Schema validation failed. See output/schema_inference_report.json")
        return 2

    print("Offline full sync pipeline completed.")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())