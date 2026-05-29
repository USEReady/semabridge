import re
from typing import Optional, List, Any
from semabridge.intermediate.models import OSIModel
from semabridge.utils.naming import sanitize_column

def strip_quotes_and_brackets(s: str) -> str:
    """Helper to clean brackets, single, and double quotes from names."""
    if not s:
        return ""
    return s.strip("[]'\"").strip()

class SemanticResolver:
    """
    Dynamically resolves DAX measures, columns, tables, and identifiers 
    to their physical database-safe SQL representations using the OSI semantic model.
    """

    @staticmethod
    def resolve_measure(name: str, osi_model: Optional[OSIModel] = None) -> str:
        """
        Locates a measure in the OSI model metrics (case-insensitively) and returns
        its uppercase sanitized database representation.
        """
        name_clean = strip_quotes_and_brackets(name)
        if not name_clean:
            return "UNKNOWN_MEASURE"

        if osi_model and osi_model.metrics:
            # Search in osi_model metrics
            for metric in osi_model.metrics:
                if metric.unique_name.casefold() == name_clean.casefold():
                    return sanitize_column(metric.unique_name, force_uppercase=True)
                for syn in getattr(metric, "synonyms", []):
                    if syn.casefold() == name_clean.casefold():
                        return sanitize_column(metric.unique_name, force_uppercase=True)

        return sanitize_column(name_clean, force_uppercase=True)

    @staticmethod
    def resolve_table(table_name: str, osi_model: Optional[OSIModel] = None) -> str:
        """
        Locates a dataset in the OSI model (case-insensitively) and returns
        its sanitized upper snake physical table name or unique name.
        """
        table_clean = strip_quotes_and_brackets(table_name)
        if not table_clean:
            return "UNKNOWN_TABLE"

        if osi_model and osi_model.datasets:
            for ds in osi_model.datasets:
                if ds.unique_name.casefold() == table_clean.casefold():
                    table_val = ds.source_table or ds.unique_name
                    return sanitize_column(table_val, force_uppercase=True)

        return sanitize_column(table_clean, force_uppercase=True)

    @staticmethod
    def resolve_column(
        table_name: Optional[str],
        column_name: str,
        osi_model: Optional[OSIModel] = None,
        table_alias: str = "FACT"
    ) -> str:
        """
        Locates the physical/source SQL column for a given dataset and logical column name.
        Uses sourceColumn metadata from OSI columns.
        """
        column_clean = strip_quotes_and_brackets(column_name)
        if not column_clean:
            return "UNKNOWN_COLUMN"

        resolved_ds = None
        if osi_model and osi_model.datasets:
            if table_name:
                table_clean = strip_quotes_and_brackets(table_name)
                for ds in osi_model.datasets:
                    if ds.unique_name.casefold() == table_clean.casefold():
                        resolved_ds = ds
                        break
            else:
                # Table not provided - lookup which dataset contains this column
                # (Prioritize the dataset matching table_alias)
                for ds in osi_model.datasets:
                    for col in ds.columns:
                        if col.unique_name.casefold() == column_clean.casefold():
                            if not resolved_ds or ds.unique_name.casefold() == table_alias.casefold():
                                resolved_ds = ds

        if resolved_ds:
            physical_table = resolved_ds.source_table or resolved_ds.unique_name
            table_ref = sanitize_column(physical_table, force_uppercase=True)

            # Find the physical column
            for col in resolved_ds.columns:
                if col.unique_name.casefold() == column_clean.casefold():
                    physical_col = col.source_expression or col.unique_name
                    physical_col = strip_quotes_and_brackets(physical_col)
                    return f"{table_ref}.{sanitize_column(physical_col, force_uppercase=True)}"
            
            # Fallback column name inside the dataset
            return f"{table_ref}.{sanitize_column(column_clean, force_uppercase=True)}"
        else:
            # Fallback when dataset is not found in osi_model
            if table_name:
                # _map_table_col behavior: uppercase table, uppercase column, no quotes
                table_ref = sanitize_column(table_name, force_uppercase=True)
                return f"{table_ref}.{sanitize_column(column_clean, force_uppercase=True)}"
            else:
                # _map_dax_col behavior: preserve table_alias casing, double quote column name
                table_ref = table_alias
                return f'{table_ref}."{sanitize_column(column_clean, force_uppercase=True)}"'

    @staticmethod
    def resolve_identifier(
        identifier: str,
        current_dataset: str,
        osi_model: Optional[OSIModel] = None
    ) -> str:
        """
        Dynamically parses and resolves a DAX identifier (measure, qualified column, or un-qualified column).
        """
        clean = identifier.strip()

        # 1. table[column] or 'table'[column] syntax
        table_col_match = re.match(
            r"^(?:'([^']+)'|([a-zA-Z0-9_#@ -]+))\[([^\]]+)\]$",
            clean,
            re.IGNORECASE
        )
        if table_col_match:
            tbl = table_col_match.group(1) or table_col_match.group(2)
            col = table_col_match.group(3)
            return SemanticResolver.resolve_column(tbl, col, osi_model, table_alias=current_dataset)

        # 2. [measure/column] syntax
        bracket_match = re.match(r"^\[([^\]]+)\]$", clean)
        if bracket_match:
            name = bracket_match.group(1).strip()
            
            # Check if it's a known metric first
            if osi_model and osi_model.metrics:
                for metric in osi_model.metrics:
                    if metric.unique_name.casefold() == name.casefold():
                        return SemanticResolver.resolve_measure(name, osi_model)

            # If not a metric, check if it's a column in current dataset
            if osi_model and current_dataset:
                ds = osi_model.get_dataset(current_dataset)
                if ds:
                    for col in ds.columns:
                        if col.unique_name.casefold() == name.casefold():
                            return SemanticResolver.resolve_column(current_dataset, name, osi_model)

            # Fallback as column in the current dataset
            return SemanticResolver.resolve_column(None, name, osi_model, table_alias=current_dataset)

        # 3. dotted references table.column or similar
        if "." in clean:
            parts = clean.split(".", 1)
            return SemanticResolver.resolve_column(parts[0], parts[1], osi_model, table_alias=current_dataset)

        # 4. Bare column or measure reference
        if osi_model and osi_model.metrics:
            for metric in osi_model.metrics:
                if metric.unique_name.casefold() == clean.casefold():
                    return SemanticResolver.resolve_measure(clean, osi_model)

        return SemanticResolver.resolve_column(None, clean, osi_model, table_alias=current_dataset)

    @staticmethod
    def discover_calendar_columns(table_name: str, osi_model: Optional[OSIModel] = None) -> dict:
        """
        Dynamically discover the Year, Period/Month, and Date columns in a calendar/date table.
        """
        result = {
            "year": "YEAR",
            "period": "PERIOD",
            "date": "DATE"
        }
        if not osi_model or not osi_model.datasets:
            return result

        ds = None
        table_clean = strip_quotes_and_brackets(table_name)
        for d in osi_model.datasets:
            if d.unique_name.casefold() == table_clean.casefold():
                ds = d
                break

        if ds:
            # 1. Discover Date column
            for col in ds.columns:
                if col.unique_name.casefold() == "date":
                    result["date"] = strip_quotes_and_brackets(col.source_expression or col.unique_name)
                    break
            else:
                for col in ds.columns:
                    if "date" in col.unique_name.casefold():
                        result["date"] = strip_quotes_and_brackets(col.source_expression or col.unique_name)
                        break

            # 2. Discover Year column
            for col in ds.columns:
                if col.unique_name.casefold() == "year":
                    result["year"] = strip_quotes_and_brackets(col.source_expression or col.unique_name)
                    break
            else:
                for col in ds.columns:
                    if "year" in col.unique_name.casefold():
                        result["year"] = strip_quotes_and_brackets(col.source_expression or col.unique_name)
                        break

            # 3. Discover Period column
            for col in ds.columns:
                if col.unique_name.casefold() in ("period", "monthno", "month"):
                    result["period"] = strip_quotes_and_brackets(col.source_expression or col.unique_name)
                    break
            else:
                for col in ds.columns:
                    if "period" in col.unique_name.casefold() or "month" in col.unique_name.casefold() or "quarter" in col.unique_name.casefold():
                        result["period"] = strip_quotes_and_brackets(col.source_expression or col.unique_name)
                        break

        # Standardize uppercase to be compatible with database requirements
        result["year"] = sanitize_column(result["year"], force_uppercase=True)
        result["period"] = sanitize_column(result["period"], force_uppercase=True)
        result["date"] = sanitize_column(result["date"], force_uppercase=True)
        return result
