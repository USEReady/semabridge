#!/usr/bin/env python3
"""
Salesforce CPQ to Databricks Semantic Layer Remediation Toolkit

Provides comprehensive diagnostics and remediation for missing foreign keys,
dimension tables, and YAML relationship misalignments in Semabridge integration.

Phase 1: Upstream ELT Pipeline Schema Synchronization
Phase 2: Databricks Lakehouse Instantiation and Deep Validation
Phase 3: Semantic Layer YAML Calibration and Redeployment
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import yaml
from collections import defaultdict

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.progress import Progress, SpinnerColumn, TextColumn
except ImportError:
    class Console:
        def print(self, *args, **kwargs): print(*args)
    Table = None
    Panel = None
    Syntax = None
    Progress = None

console = Console()


# ============================================================================
# SALESFORCE CPQ FIELD REGISTRY
# ============================================================================

@dataclass
class SFDCField:
    """Salesforce field definition"""
    api_name: str
    label: str
    data_type: str
    is_lookup: bool = False
    lookup_target: Optional[str] = None
    is_custom: bool = False
    is_system_generated: bool = False
    description: str = ""


class SFDCCPQFieldRegistry:
    """Authoritative registry of SBQQ__Quote__c foreign keys and critical fields"""
    
    # Standard managed package fields (SBQQ*)
    STANDARD_FIELDS = {
        "Id": SFDCField("Id", "Quote ID", "Text", is_system_generated=True),
        "Name": SFDCField("Name", "Quote Name", "Text"),
        "SBQQ__Opportunity2__c": SFDCField(
            "SBQQ__Opportunity2__c",
            "Opportunity",
            "Lookup",
            is_lookup=True,
            lookup_target="Opportunity",
            description="Primary FK to Opportunity (replaces legacy Master-Detail)"
        ),
        "SBQQ__Account__c": SFDCField(
            "SBQQ__Account__c",
            "Account",
            "Lookup",
            is_lookup=True,
            lookup_target="Account",
            description="Standard FK to Account"
        ),
        "SBQQ__PrimaryQuote__c": SFDCField(
            "SBQQ__PrimaryQuote__c",
            "Primary Quote",
            "Lookup",
            is_lookup=True,
            lookup_target="SBQQ__Quote__c",
            description="Recursive FK to primary quote"
        ),
        "SBQQ__OriginalQuote__c": SFDCField(
            "SBQQ__OriginalQuote__c",
            "Original Quote",
            "Lookup",
            is_lookup=True,
            lookup_target="SBQQ__Quote__c",
            description="Amendment lineage: links amended quote to original"
        ),
        "SBQQ__OrderGroupID__c": SFDCField(
            "SBQQ__OrderGroupID__c",
            "Order Group ID",
            "Text",
            is_system_generated=True,
            description="Transaction lineage identifier for amend/re-quote"
        ),
        "SBQQ__RegularAmount__c": SFDCField(
            "SBQQ__RegularAmount__c",
            "Regular Amount",
            "Currency",
            description="Total amount excluding discounts"
        ),
        "SBQQ__Ordered__c": SFDCField(
            "SBQQ__Ordered__c",
            "Ordered",
            "Checkbox",
            description="Triggers CPX Order record generation"
        ),
        "SBQQ__Renewal__c": SFDCField(
            "SBQQ__Renewal__c",
            "Renewal",
            "Checkbox",
            description="TRUE if auto-generated from Contract"
        ),
    }

    # Enterprise custom dimension fields (expected in logs as missing)
    EXPECTED_CUSTOM_FIELDS = {
        "Customer_Project__c": SFDCField(
            "Customer_Project__c",
            "Customer Project",
            "Lookup",
            is_lookup=True,
            lookup_target="Customer_Project__c",
            is_custom=True,
            description="Custom FK to enterprise project dimension"
        ),
        "Intake_Form__c": SFDCField(
            "Intake_Form__c",
            "Intake Form",
            "Lookup",
            is_lookup=True,
            lookup_target="Intake_Form__c",
            is_custom=True,
            description="Custom FK to intake form tracking"
        ),
        "Building_Code__c": SFDCField(
            "Building_Code__c",
            "Building Code",
            "Lookup",
            is_lookup=True,
            lookup_target="Building_Code__c",
            is_custom=True,
            description="Custom FK to building dimension"
        ),
    }

    @classmethod
    def get_all_foreign_keys(cls) -> Dict[str, SFDCField]:
        """Return all FK fields (both standard and custom)"""
        return {
            **{k: v for k, v in cls.STANDARD_FIELDS.items() if v.is_lookup},
            **cls.EXPECTED_CUSTOM_FIELDS
        }

    @classmethod
    def get_required_dimension_tables(cls) -> Dict[str, str]:
        """Map lookup_target to Databricks table naming convention"""
        mapping = {}
        for field_def in cls.get_all_foreign_keys().values():
            if field_def.lookup_target:
                # Convert Salesforce object name to Databricks table name
                db_table = f"REP_SFDC_{field_def.lookup_target.upper().replace('__C', '_C')}"
                mapping[field_def.lookup_target] = db_table
        return mapping


# ============================================================================
# PHASE 1: ELT EXTRACTION SCRIPT GENERATOR
# ============================================================================

class ELTExtractionScriptGenerator:
    """Generate or suggest corrected ELT replication scripts"""
    
    TEMPLATE_SOQL = """-- Corrected SOQL for SBQQ__Quote__c extraction
-- Include ALL required foreign keys to support semantic layer joins

SELECT 
    Id,
    Name,
    CreatedDate,
    LastModifiedDate,
    __STANDARD_FKS__,
    __CUSTOM_FKS__
FROM SBQQ__Quote__c
WHERE CreatedDate >= {env:start_extraction_date}
  AND LastModifiedDate <= {env:end_extraction_date}
ORDER BY LastModifiedDate DESC
"""

    TEMPLATE_SQL = """-- SQL Pattern for CData Sync or similar tools
-- Update this query in your replication configuration

SELECT [Id], 
       [Name], 
       [CreatedDate], 
       [LastModifiedDate],
       __STANDARD_FKS__,
       __CUSTOM_FKS__
FROM [SBQQ_Quote__c]
WHERE [LastModifiedDate] >= '{env:start_extraction_date}'
  AND [LastModifiedDate] <= '{env:end_extraction_date}'
ORDER BY [LastModifiedDate] DESC
"""

    @staticmethod
    def generate_soql_query(include_custom: bool = True) -> str:
        """Generate corrected SOQL query for Quote extraction"""
        registry = SFDCCPQFieldRegistry()
        
        standard_fks = [
            f.api_name for f in registry.get_all_foreign_keys().values()
            if not f.is_custom
        ]
        custom_fks = [
            f.api_name for f in registry.get_all_foreign_keys().values()
            if f.is_custom and include_custom
        ]
        
        result = ELTExtractionScriptGenerator.TEMPLATE_SOQL
        result = result.replace("__STANDARD_FKS__", ",\n    ".join(standard_fks))
        result = result.replace("__CUSTOM_FKS__", ",\n    ".join(custom_fks) if custom_fks else "-- NO CUSTOM FIELDS CONFIGURED")
        return result

    @staticmethod
    def generate_sql_query(include_custom: bool = True) -> str:
        """Generate SQL pattern for CData Sync or similar"""
        registry = SFDCCPQFieldRegistry()
        
        standard_fks = [
            f"[{f.api_name}]" for f in registry.get_all_foreign_keys().values()
            if not f.is_custom
        ]
        custom_fks = [
            f"[{f.api_name}]" for f in registry.get_all_foreign_keys().values()
            if f.is_custom and include_custom
        ]
        
        result = ELTExtractionScriptGenerator.TEMPLATE_SQL
        result = result.replace("__STANDARD_FKS__", ",\n       ".join(standard_fks))
        result = result.replace(
            "__CUSTOM_FKS__",
            ",\n       ".join(custom_fks) if custom_fks else "-- NO CUSTOM FIELDS CONFIGURED",
        )
        return result


# ============================================================================
# PHASE 2: DATABRICKS LAKEHOUSE AUDIT & VALIDATION
# ============================================================================

@dataclass
class TableAuditResult:
    """Result of table existence/schema audit"""
    table_name: str
    exists: bool
    column_count: int = 0
    missing_columns: List[str] = None
    sample_rows: int = 0
    
    def __post_init__(self):
        if self.missing_columns is None:
            self.missing_columns = []


@dataclass
class ForeignKeyAuditResult:
    """Result of foreign key population audit"""
    fk_field: str
    source_table: str
    target_table: str
    total_rows: int = 0
    populated_count: int = 0
    null_count: int = 0
    null_percentage: float = 0.0
    missing_column: bool = False


class DatabricksSchemaAuditor:
    """
    Audits Databricks lakehouse for:
    - Existence of required dimension tables
    - Presence of all foreign key columns
    - Population rate of foreign keys (null vs populated)
    - Schema drift from source
    """
    
    def __init__(self, spark=None, catalog: str = "semabridge", schema: str = "public"):
        """Initialize with Spark session or connection string"""
        self.spark = spark
        self.catalog = catalog
        self.schema_name = schema
        self.audit_results: Dict[str, TableAuditResult] = {}
        self.fk_audit_results: List[ForeignKeyAuditResult] = []

    def audit_required_tables(self) -> Dict[str, TableAuditResult]:
        """Audit existence and basic properties of required dimension tables"""
        registry = SFDCCPQFieldRegistry()
        required_tables = registry.get_required_dimension_tables()
        
        results = {}
        for sf_object, db_table in required_tables.items():
            result = TableAuditResult(table_name=db_table, exists=False)
            
            # Mock audit (replace with actual Spark SQL if available)
            console.print(f"[yellow]Checking {db_table}...[/yellow]")
            
            # Result would come from: SHOW TABLES IN schema LIKE pattern
            results[sf_object] = result
        
        self.audit_results = results
        return results

    def audit_quote_foreign_keys(self) -> List[ForeignKeyAuditResult]:
        """Audit foreign key population rate in Quote table"""
        results = []
        registry = SFDCCPQFieldRegistry()
        
        quote_table = f"{self.catalog}.{self.schema_name}.REP_SFDC_SBQQ__QUOTE__C"
        fk_fields = registry.get_all_foreign_keys()
        
        for api_name, field_def in fk_fields.items():
            if not field_def.lookup_target:
                continue
                
            result = ForeignKeyAuditResult(
                fk_field=api_name,
                source_table=quote_table,
                target_table=f"{self.catalog}.{self.schema_name}.REP_SFDC_{field_def.lookup_target.upper().replace('__C', '_C')}"
            )
            results.append(result)
        
        self.fk_audit_results = results
        return results

    def generate_audit_sql(self) -> Dict[str, str]:
        """Generate SQL queries to manually run for auditing"""
        queries = {}
        registry = SFDCCPQFieldRegistry()
        
        # Table existence check
        queries["table_existence"] = f"""
-- Check which SFDC tables exist in Databricks
SELECT 
    table_schema,
    table_name,
    table_type
FROM {self.catalog}.information_schema.tables
WHERE table_schema = '{self.schema_name}'
  AND table_name LIKE 'REP_SFDC_%'
ORDER BY table_name;
"""
        
        # Foreign key population analysis for Quote table
        fk_fields = registry.get_all_foreign_keys()
        fk_checks = []
        for api_name in fk_fields.keys():
            fk_checks.append(f"""
    COUNT(CASE WHEN {api_name} IS NOT NULL THEN 1 END) as {api_name}_populated,
    COUNT(CASE WHEN {api_name} IS NULL THEN 1 END) as {api_name}_null,
    ROUND(100.0 * COUNT(CASE WHEN {api_name} IS NULL THEN 1 END) / COUNT(*), 2) as {api_name}_null_pct""")
        
        queries["quote_fk_population"] = f"""
-- Analyze foreign key population in Quote table
SELECT 
    COUNT(*) as total_rows,
    {','.join(fk_checks)}
FROM {self.catalog}.{self.schema_name}.REP_SFDC_SBQQ__QUOTE__C;
"""
        
        # Schema comparison
        queries["quote_schema"] = f"""
-- Current schema of Quote table
DESCRIBE {self.catalog}.{self.schema_name}.REP_SFDC_SBQQ__QUOTE__C;
"""
        
        return queries


# ============================================================================
# PHASE 3: DDL GENERATION FOR MISSING TABLES
# ============================================================================

class DatabricksDDLGenerator:
    """Generate DDL for missing dimension tables"""
    
    # Base schema definitions for common Salesforce objects
    DIMENSION_SCHEMAS = {
        "Opportunity": {
            "Id": "STRING NOT NULL",
            "Name": "STRING",
            "AccountId": "STRING",
            "Amount": "DECIMAL(18,2)",
            "StageName": "STRING",
            "CloseDate": "DATE",
            "CreatedDate": "TIMESTAMP",
            "LastModifiedDate": "TIMESTAMP",
        },
        "Account": {
            "Id": "STRING NOT NULL",
            "Name": "STRING",
            "AccountNumber": "STRING",
            "BillingCity": "STRING",
            "BillingCountry": "STRING",
            "CreatedDate": "TIMESTAMP",
            "LastModifiedDate": "TIMESTAMP",
        },
        "Customer_Project__c": {
            "Id": "STRING NOT NULL",
            "Name": "STRING",
            "Project_Code__c": "STRING",
            "Deal_Score__c": "DECIMAL(10,2)",
            "Deal_Scope__c": "STRING",
            "CreatedDate": "TIMESTAMP",
            "LastModifiedDate": "TIMESTAMP",
        },
        "Intake_Form__c": {
            "Id": "STRING NOT NULL",
            "Name": "STRING",
            "Form_Type__c": "STRING",
            "CreatedDate": "TIMESTAMP",
            "LastModifiedDate": "TIMESTAMP",
        },
        "Building_Code__c": {
            "Id": "STRING NOT NULL",
            "Code": "STRING",
            "Description": "STRING",
            "Location__c": "STRING",
            "CreatedDate": "TIMESTAMP",
            "LastModifiedDate": "TIMESTAMP",
        },
    }

    @staticmethod
    def generate_table_ddl(
        sf_object: str,
        catalog: str = "semabridge",
        schema: str = "public",
        use_delta: bool = True
    ) -> str:
        """Generate CREATE TABLE DDL for dimension table"""
        
        if sf_object not in DatabricksDDLGenerator.DIMENSION_SCHEMAS:
            return f"-- WARNING: No schema template for {sf_object}"
        
        db_table_name = f"REP_SFDC_{sf_object.upper().replace('__C', '_C')}"
        schema_def = DatabricksDDLGenerator.DIMENSION_SCHEMAS[sf_object]
        
        column_defs = ",\n    ".join(
            f"{col_name} {col_type}" for col_name, col_type in schema_def.items()
        )
        
        table_format = "USING DELTA" if use_delta else ""
        
        ddl = f"""
-- Create dimension table for {sf_object}
CREATE TABLE IF NOT EXISTS {catalog}.{schema}.{db_table_name} (
    {column_defs}
)
{table_format}
PARTITIONED BY (year(LastModifiedDate))
TBLPROPERTIES (
    'description' = 'Replicated Salesforce {sf_object} dimension',
    'source_system' = 'Salesforce',
    'source_object' = '{sf_object}',
    'extraction_date' = CURRENT_TIMESTAMP()
);
"""
        return ddl

    @staticmethod
    def generate_all_missing_tables_ddl(
        catalog: str = "semabridge",
        schema: str = "public"
    ) -> str:
        """Generate DDL script for all required Salesforce dimension tables"""
        
        registry = SFDCCPQFieldRegistry()
        required_objects = set(
            f.lookup_target for f in registry.get_all_foreign_keys().values()
            if f.lookup_target and f.is_lookup
        )
        
        ddl_statements = [
            "-- ============================================================================",
            "-- Salesforce CPQ Dimension Tables DDL",
            "-- ============================================================================",
            "-- Execute these statements to create missing dimension tables in Databricks",
            "",
        ]
        
        for sf_object in sorted(required_objects):
            ddl_statements.append(
                DatabricksDDLGenerator.generate_table_ddl(sf_object, catalog, schema)
            )
        
        return "\n".join(ddl_statements)


# ============================================================================
# PHASE 3: YAML RELATIONSHIP VALIDATOR
# ============================================================================

@dataclass
class YAMLValidationIssue:
    """Issue found in YAML configuration"""
    severity: str  # "ERROR", "WARNING", "INFO"
    field: str
    issue: str
    suggestion: str


class YAMLRelationshipValidator:
    """Validate semantic YAML relationships against physical schema"""
    
    def __init__(self, yaml_path: str):
        self.yaml_path = Path(yaml_path)
        self.config = {}
        self.issues: List[YAMLValidationIssue] = []
        
        if self.yaml_path.exists():
            with open(self.yaml_path) as f:
                self.config = yaml.safe_load(f) or {}

    def validate_relationships(self) -> List[YAMLValidationIssue]:
        """Validate all relationship definitions"""
        registry = SFDCCPQFieldRegistry()
        issues = []
        
        # Check for missing relationships
        expected_fks = registry.get_all_foreign_keys()
        config_relationships = self.config.get('relationships', {})
        
        for fk_api_name, field_def in expected_fks.items():
            if not field_def.is_lookup:
                continue
            
            # Check if relationship is defined
            rel_found = any(
                fk_api_name in str(rel) 
                for rel in config_relationships.values()
            )
            
            if not rel_found:
                issues.append(YAMLValidationIssue(
                    severity="ERROR",
                    field=fk_api_name,
                    issue=f"Foreign key relationship not defined in YAML",
                    suggestion=f"Add relationship definition for {fk_api_name} → {field_def.lookup_target}"
                ))
        
        self.issues = issues
        return issues

    def generate_relationship_yaml(self) -> str:
        """Generate corrected relationship definitions for YAML"""
        registry = SFDCCPQFieldRegistry()
        fk_fields = registry.get_all_foreign_keys()
        
        yaml_items = {
            "relationships": {}
        }
        
        for api_name, field_def in fk_fields.items():
            if not field_def.is_lookup:
                continue
            
            db_table = f"REP_SFDC_{field_def.lookup_target.upper().replace('__C', '_C')}"
            rel_name = f"REL_QUOTE_TO_{field_def.lookup_target.upper().replace('__C', '')}"
            
            yaml_items["relationships"][rel_name] = {
                "joining_dimension": db_table,
                "force_grouping": False,
                "cardinality": "many_to_one",
                "join_keys": [
                    {
                        "dimension_field": "Id",
                        "foreign_key": api_name,
                        "type": "source_key_to_dimension_key"
                    }
                ]
            }
        
        return yaml.dump(yaml_items, default_flow_style=False, sort_keys=False)


# ============================================================================
# CLI INTERFACE
# ============================================================================

def print_header(title: str):
    """Print formatted header"""
    console.print(f"\n[bold cyan]{'=' * 80}[/bold cyan]")
    console.print(f"[bold cyan]{title.center(80)}[/bold cyan]")
    console.print(f"[bold cyan]{'=' * 80}[/bold cyan]\n")


def cmd_audit_phase2(args):
    """Run Phase 2 (Databricks) audit"""
    print_header("PHASE 2: Databricks Lakehouse Schema Audit")
    
    auditor = DatabricksSchemaAuditor()
    
    console.print("[bold]Generated Audit SQL Queries:[/bold]\n")
    for query_name, query in auditor.generate_audit_sql().items():
        console.print(f"[yellow]--- {query_name} ---[/yellow]")
        if Syntax:
            console.print(Syntax(query, "sql", theme="monokai", line_numbers=True))
        else:
            console.print(query)
        console.print()


def cmd_generate_elt(args):
    """Generate Phase 1 (ELT) extraction scripts"""
    print_header("PHASE 1: ELT Extraction Script Generation")
    
    console.print("[bold]SOQL Pattern:[/bold]\n")
    soql = ELTExtractionScriptGenerator.generate_soql_query()
    if Syntax:
        console.print(Syntax(soql, "sql", theme="monokai", line_numbers=True))
    else:
        console.print(soql)
    
    console.print("\n[bold]SQL Pattern (for CData Sync, etc):[/bold]\n")
    sql = ELTExtractionScriptGenerator.generate_sql_query()
    if Syntax:
        console.print(Syntax(sql, "sql", theme="monokai", line_numbers=True))
    else:
        console.print(sql)


def cmd_generate_ddl(args):
    """Generate Phase 2 (DDL) for missing tables"""
    print_header("PHASE 2: Databricks DDL Generation")
    
    ddl = DatabricksDDLGenerator.generate_all_missing_tables_ddl()
    
    console.print("[bold]Execute these SQL statements in Databricks:[/bold]\n")
    if Syntax:
        console.print(Syntax(ddl, "sql", theme="monokai", line_numbers=True))
    else:
        console.print(ddl)
    
    # Save to file
    output_path = Path("databricks_dimension_tables.sql")
    output_path.write_text(ddl)
    console.print(f"\n[green]✓[/green] Saved to [cyan]{output_path.absolute()}[/cyan]")


def cmd_validate_yaml(args):
    """Validate Phase 3 YAML relationships"""
    print_header("PHASE 3: Semantic Layer YAML Validation")
    
    yaml_path = args.yaml or "Config/semabridge.yaml"
    
    validator = YAMLRelationshipValidator(yaml_path)
    issues = validator.validate_relationships()
    
    if issues:
        console.print(f"[red]Found {len(issues)} issues:[/red]\n")
        for issue in issues:
            severity_color = {
                "ERROR": "red",
                "WARNING": "yellow",
                "INFO": "blue"
            }.get(issue.severity, "white")
            
            console.print(f"[{severity_color}][{issue.severity}][/{severity_color}] {issue.field}")
            console.print(f"  Issue: {issue.issue}")
            console.print(f"  Suggestion: {issue.suggestion}\n")
    else:
        console.print("[green]✓ All relationships validated successfully![/green]")
    
    console.print("\n[bold]Generated Relationship YAML:[/bold]\n")
    yaml_text = validator.generate_relationship_yaml()
    console.print(yaml_text)


def cmd_field_registry(args):
    """Display Salesforce CPQ field registry"""
    print_header("Salesforce CPQ Field Registry")
    
    registry = SFDCCPQFieldRegistry()
    
    # Standard fields
    console.print("[bold cyan]Standard SBQQ* Fields (System):[/bold cyan]\n")
    
    table = Table(title="Standard Fields")
    table.add_column("API Name", style="cyan")
    table.add_column("Label", style="green")
    table.add_column("Type", style="yellow")
    table.add_column("Description")
    
    for api_name, field in registry.STANDARD_FIELDS.items():
        table.add_row(
            api_name,
            field.label,
            "Lookup" if field.is_lookup else field.data_type,
            field.description
        )
    
    if Table:
        console.print(table)
    else:
        for api_name, field in registry.STANDARD_FIELDS.items():
            console.print(f"{api_name}: {field.label} ({field.data_type})")
    
    # Custom fields
    console.print("\n[bold red]Expected Custom Dimension Fields:[/bold red]\n")
    
    table = Table(title="Custom Fields")
    table.add_column("API Name", style="magenta")
    table.add_column("Label", style="green")
    table.add_column("Target", style="cyan")
    table.add_column("Description")
    
    for api_name, field in registry.EXPECTED_CUSTOM_FIELDS.items():
        table.add_row(
            api_name,
            field.label,
            field.lookup_target,
            field.description
        )
    
    if Table:
        console.print(table)
    else:
        for api_name, field in registry.EXPECTED_CUSTOM_FIELDS.items():
            console.print(f"{api_name}: {field.label} → {field.lookup_target}")


def main():
    """Main CLI entry point"""
    
    if len(sys.argv) < 2:
        print_header("Salesforce CPQ to Databricks Remediation Toolkit")
        console.print("""[bold]Usage:[/bold]

  python sfdc_cpq_remediation.py <command> [options]

[bold]Commands:[/bold]

  audit-phase2          Run Databricks schema audit (generates audit SQL)
  generate-elt          Generate corrected ELT extraction templates
  generate-ddl          Generate DDL for missing Snowflake dimension tables
  validate-yaml         Validate semantic YAML relationship definitions
  field-registry        Display Salesforce CPQ field registry

[bold]Example workflow:[/bold]

  1. python sfdc_cpq_remediation.py field-registry
     → Review which fields are expected

  2. python sfdc_cpq_remediation.py audit-phase2
     → Copy/run audit SQL in Databricks to identify missing columns

  3. python sfdc_cpq_remediation.py generate-elt
     → Update your ELT pipeline extraction queries

  4. python sfdc_cpq_remediation.py generate-ddl
     → Create missing dimension tables in Databricks

  5. python sfdc_cpq_remediation.py validate-yaml
     → Fix relationship definitions in semabridge.yaml
""")
        return
    
    cmd = sys.argv[1]
    
    # Create args object
    class Args:
        yaml = None
    args = Args()
    
    # Parse options
    for i, arg in enumerate(sys.argv[2:]):
        if arg == "--yaml" and i + 2 < len(sys.argv):
            args.yaml = sys.argv[i + 3]
    
    # Dispatch commands
    commands = {
        "audit-phase2": cmd_audit_phase2,
        "generate-elt": cmd_generate_elt,
        "generate-ddl": cmd_generate_ddl,
        "validate-yaml": cmd_validate_yaml,
        "field-registry": cmd_field_registry,
    }
    
    if cmd not in commands:
        console.print(f"[red]Unknown command: {cmd}[/red]")
        console.print(f"[yellow]Available: {', '.join(commands.keys())}[/yellow]")
        sys.exit(1)
    
    try:
        commands[cmd](args)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if "--debug" in sys.argv:
            raise
        sys.exit(1)


if __name__ == "__main__":
    main()
