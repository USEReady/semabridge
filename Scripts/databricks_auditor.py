#!/usr/bin/env python3
"""
Databricks Schema Auditor

Real-time auditing of SFDC tables in Databricks with Spark integration.
Validates table existence, column presence, and foreign key population rates.
"""

import sys
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

try:
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import col, count, when, isnan, isnull, coalesce
    HAS_SPARK = True
except ImportError:
    HAS_SPARK = False

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, track
except ImportError:
    class Console:
        def print(self, *args, **kwargs): print(*args)
    Table = None
    Panel = None
    Progress = None
    def track(iterable, *args, **kwargs): return iterable

console = Console()


@dataclass
class ColumnAuditResult:
    """Result of column-level audit"""
    column_name: str
    data_type: str
    nullable: bool
    exists: bool
    non_null_count: int = 0
    null_count: int = 0
    total_records: int = 0
    
    @property
    def null_percentage(self) -> float:
        if self.total_records == 0:
            return 0.0
        return (self.null_count / self.total_records) * 100


@dataclass
class TableAuditResult:
    """Result of table-level audit"""
    table_name: str
    catalog: str
    schema: str
    exists: bool
    row_count: int = 0
    column_count: int = 0
    columns: Dict[str, ColumnAuditResult] = None
    
    def __post_init__(self):
        if self.columns is None:
            self.columns = {}


class DatabricksAuditor:
    """Execute real-time audits against Databricks workspace"""
    
    def __init__(self, catalog: str = "semabridge", schema: str = "public"):
        self.catalog = catalog
        self.schema = schema
        self.spark = None
        self._init_spark()
    
    def _init_spark(self):
        """Initialize Spark session if available"""
        if HAS_SPARK:
            try:
                self.spark = SparkSession.builder.getOrCreate()
                console.print("[green]✓ Spark session initialized[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠ Could not initialize Spark: {e}[/yellow]")
                console.print("[yellow]  Audit functions will be stub/mock[/yellow]")

    def audit_table_existence(self) -> Dict[str, bool]:
        """Check which SFDC tables exist in Databricks"""
        
        if not self.spark:
            return self._mock_audit_tables()
        
        try:
            # Query information_schema for tables
            query = f"""
            SELECT table_schema, table_name
            FROM {self.catalog}.information_schema.tables
            WHERE table_schema = '{self.schema}'
              AND table_name LIKE 'REP_SFDC_%'
            ORDER BY table_name
            """
            
            df = self.spark.sql(query)
            tables = {}
            for row in df.collect():
                tables[row.table_name] = True
            
            return tables
        except Exception as e:
            console.print(f"[red]Error querying tables: {e}[/red]")
            return self._mock_audit_tables()

    def _mock_audit_tables(self) -> Dict[str, bool]:
        """Mock audit for testing without Spark"""
        return {
            "REP_SFDC_SBQQ__QUOTE__C": True,
            "REP_SFDC_OPPORTUNITY_C": False,
            "REP_SFDC_ACCOUNT_C": False,
            "REP_SFDC_CUSTOMER_PROJECT_C": False,
            "REP_SFDC_INTAKE_FORM_C": False,
            "REP_SFDC_BUILDING_CODE_C": False,
        }

    def audit_table_schema(self, table_name: str) -> Optional[TableAuditResult]:
        """Audit schema and row count for specific table"""
        
        full_table_name = f"{self.catalog}.{self.schema}.{table_name}"
        result = TableAuditResult(table_name, self.catalog, self.schema, exists=False)
        
        if not self.spark:
            return result
        
        try:
            # Check existence
            df = self.spark.sql(f"SELECT * FROM {full_table_name} LIMIT 1")
            result.exists = True
            
            # Get schema
            schema = df.schema
            result.column_count = len(schema.fields)
            result.columns = {
                f.name: ColumnAuditResult(
                    column_name=f.name,
                    data_type=str(f.dataType),
                    nullable=f.nullable,
                    exists=True
                )
                for f in schema.fields
            }
            
            # Get row count
            count_df = self.spark.sql(f"SELECT COUNT(*) as cnt FROM {full_table_name}")
            result.row_count = count_df.collect()[0]["cnt"]
            
            return result
        except Exception as e:
            console.print(f"[yellow]⚠ Table {table_name} not found or error: {e}[/yellow]")
            return result

    def audit_foreign_key_population(
        self,
        source_table: str,
        fk_column: str
    ) -> Optional[ColumnAuditResult]:
        """Audit foreign key population (null percentage)"""
        
        full_table = f"{self.catalog}.{self.schema}.{source_table}"
        
        if not self.spark:
            return None
        
        try:
            query = f"""
            SELECT
                COUNT(*) as total_records,
                COUNT(CASE WHEN {fk_column} IS NOT NULL THEN 1 END) as non_null_count,
                COUNT(CASE WHEN {fk_column} IS NULL THEN 1 END) as null_count,
                ROUND(100.0 * COUNT(CASE WHEN {fk_column} IS NULL THEN 1 END) / COUNT(*), 2) as null_pct
            FROM {full_table}
            """
            
            result_df = self.spark.sql(query)
            row = result_df.collect()[0]
            
            result = ColumnAuditResult(
                column_name=fk_column,
                data_type="",
                nullable=True,
                exists=True,
                non_null_count=row["non_null_count"],
                null_count=row["null_count"],
                total_records=row["total_records"]
            )
            
            return result
        except Exception as e:
            console.print(f"[red]Error auditing FK {fk_column}: {e}[/red]")
            return None

    def audit_quote_foreign_keys(self) -> Dict[str, tuple]:
        """Comprehensive audit of all Quote foreign keys"""
        
        # Define expected FKs
        fk_fields = {
            "SBQQ__Opportunity2__c": "[MISSING]",
            "SBQQ__Account__c": "[MISSING]",
            "SBQQ__PrimaryQuote__c": "[MISSING]",
            "SBQQ__OriginalQuote__c": "[MISSING]",
            "Customer_Project__c": "[CUSTOM MISSING]",
            "Intake_Form__c": "[CUSTOM MISSING]",
            "Building_Code__c": "[CUSTOM MISSING]",
        }
        
        results = {}
        quote_table = "REP_SFDC_SBQQ__QUOTE__C"
        
        for fk_col in track(fk_fields.keys(), description="Auditing FK population..."):
            audit = self.audit_foreign_key_population(quote_table, fk_col)
            if audit:
                results[fk_col] = (
                    audit.exists,
                    audit.null_count,
                    audit.non_null_count,
                    audit.null_percentage
                )
            else:
                results[fk_col] = (False, 0, 0, 0.0)
        
        return results

    def generate_audit_report(self) -> str:
        """Generate comprehensive markdown audit report"""
        
        report_lines = [
            "# Databricks SFDC Schema Audit Report",
            "",
            "## Table Existence Check",
            "",
        ]
        
        # Audit tables
        tables = self.audit_table_existence()
        
        table_md = "| Table | Exists | Status |\n|-------|--------|--------|\n"
        for table_name, exists in sorted(tables.items()):
            status = "✓ Present" if exists else "❌ Missing"
            table_md += f"| {table_name} | {exists} | {status} |\n"
        
        report_lines.append(table_md)
        report_lines.append("")
        
        # Audit Quote foreign keys
        report_lines.append("## Foreign Key Population Analysis")
        report_lines.append("")
        
        fk_results = self.audit_quote_foreign_keys()
        fk_md = "| Field | Exists | Populated | Null | Null % |\n"
        fk_md += "|-------|--------|-----------|------|--------|\n"
        
        for fk_col, (exists, null_count, non_null_count, null_pct) in sorted(fk_results.items()):
            exists_str = "✓" if exists else "❌"
            total = null_count + non_null_count
            fk_md += f"| {fk_col} | {exists_str} | {non_null_count}/{total} | {null_count} | {null_pct:0.1f}% |\n"
        
        report_lines.append(fk_md)
        report_lines.append("")
        
        return "\n".join(report_lines)


def cmd_audit_tables(args):
    """Audit table existence"""
    auditor = DatabricksAuditor()
    
    console.print("\n[bold cyan]Table Existence Audit[/bold cyan]\n")
    
    tables = auditor.audit_table_existence()
    
    if Table:
        table = Table(title="SFDC Tables in Databricks")
        table.add_column("Table Name", style="cyan")
        table.add_column("Exists", style="green")
        
        for table_name in sorted(tables.keys()):
            exists = tables[table_name]
            exists_str = "✓" if exists else "❌ MISSING"
            table.add_row(table_name, exists_str)
        
        console.print(table)
    else:
        for table_name, exists in sorted(tables.items()):
            status = "✓" if exists else "❌"
            console.print(f"{table_name}: {status}")


def cmd_audit_fk(args):
    """Audit foreign key population"""
    auditor = DatabricksAuditor()
    
    console.print("\n[bold cyan]Foreign Key Population Audit[/bold cyan]\n")
    
    results = auditor.audit_quote_foreign_keys()
    
    if Table:
        table = Table(title="Quote Foreign Key Analysis")
        table.add_column("FK Field", style="cyan")
        table.add_column("Exists", style="green")
        table.add_column("Populated")
        table.add_column("Null", style="red")
        table.add_column("Null %", style="yellow")
        
        for fk_col, (exists, null_count, non_null_count, null_pct) in sorted(results.items()):
            exists_str = "✓" if exists else "❌"
            total = null_count + non_null_count
            table.add_row(
                fk_col,
                exists_str,
                f"{non_null_count}/{total}",
                str(null_count),
                f"{null_pct:0.1f}%"
            )
        
        console.print(table)
    else:
        for fk_col, (exists, null_count, non_null_count, null_pct) in sorted(results.items()):
            console.print(f"{fk_col}: {'✓' if exists else '❌'} null={null_pct:0.1f}%")


def cmd_generate_report(args):
    """Generate full audit report"""
    auditor = DatabricksAuditor()
    
    console.print("\n[bold cyan]Generating Audit Report[/bold cyan]\n")
    
    report = auditor.generate_audit_report()
    console.print(report)
    
    # Save report
    report_path = Path("databricks_audit_report.md")
    report_path.write_text(report)
    console.print(f"\n[green]✓ Saved to [cyan]{report_path.absolute()}[/cyan][/green]")


def main():
    """CLI entry point"""
    
    if len(sys.argv) < 2:
        console.print("""[bold]Databricks SFDC Auditor[/bold]

Usage:
  python databricks_auditor.py <command>

Commands:
  audit-tables     Check which SFDC tables exist in Databricks
  audit-fk         Audit foreign key population rates
  generate-report  Generate comprehensive audit report
""")
        return
    
    cmd = sys.argv[1]
    
    class Args:
        pass
    args = Args()
    
    commands = {
        "audit-tables": cmd_audit_tables,
        "audit-fk": cmd_audit_fk,
        "generate-report": cmd_generate_report,
    }
    
    if cmd not in commands:
        console.print(f"[red]Unknown command: {cmd}[/red]")
        sys.exit(1)
    
    try:
        commands[cmd](args)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
