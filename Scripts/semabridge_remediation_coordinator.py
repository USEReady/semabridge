#!/usr/bin/env python3
"""
Semabridge Integration Remediation Coordinator

High-level orchestration of remediation phases with Semabridge API integration.
Validates configuration, identifies issues, and provides guided remediation.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import yaml

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, track
    from rich.syntax import Syntax
except ImportError:
    class Console:
        def print(self, *args, **kwargs): print(*args)
    Table = None
    Panel = None
    Progress = None
    def track(iterable, *args, **kwargs): return iterable
    Syntax = None

console = Console()


@dataclass
class RemediationStep:
    """Individual remediation action"""
    phase: int
    step: int
    title: str
    description: str
    commands: List[str]
    sql_required: bool = False
    yaml_required: bool = False
    validation: Optional[str] = None
    estimated_time: str = "5 min"


class RemediationPlan:
    """Comprehensive remediation execution plan"""
    
    PHASES = {
        1: {
            "title": "ELT Pipeline Schema Synchronization",
            "description": "Update Salesforce data extraction queries to include all required foreign keys",
            "status": "NOT_STARTED"
        },
        2: {
            "title": "Databricks Lakehouse Materialization",
            "description": "Create missing dimension tables and validate schema in Databricks",
            "status": "NOT_STARTED"
        },
        3: {
            "title": "Semantic Layer YAML Calibration",
            "description": "Update Semabridge relationship definitions to match physical schema",
            "status": "NOT_STARTED"
        }
    }
    
    def __init__(self):
        self.config_path = Path("Config/semabridge.yaml")
        self.steps: List[RemediationStep] = []
        self._build_plan()
    
    def _build_plan(self):
        """Build complete remediation plan"""
        
        # Phase 1: ELT
        self.steps.append(RemediationStep(
            phase=1,
            step=1,
            title="Review Current Extraction Configuration",
            description="Examine existing ELT/replication scripts in Salesforce extraction tool",
            commands=[
                "python Scripts/sfdc_cpq_remediation.py generate-elt",
                "# Review the SOQL and SQL templates generated above"
            ],
            sql_required=False,
            estimated_time="10 min"
        ))
        
        self.steps.append(RemediationStep(
            phase=1,
            step=2,
            title="Update ELT Extraction Query",
            description="Modify SOQL/SQL query in your Salesforce replication tool to include all FSK columns",
            commands=[
                "# Update extraction query in CData Sync, UiPath, Airbyte, or custom tool", 
                "# Include: SBQQ__Opportunity2__c, SBQQ__Account__c, Customer_Project__c, etc.",
                "# Re-run initial load to pull complete schema"
            ],
            sql_required=False,
            estimated_time="20 min"
        ))
        
        self.steps.append(RemediationStep(
            phase=1,
            step=3,
            title="Verify Extraction in Staging",
            description="Confirm new columns appear in staging/temporary Databricks tables",
            commands=[
                "SELECT TOP 5 * FROM semabridge.staging.REP_SFDC_SBQQ__QUOTE__C",
                "# Verify SBQQ__Opportunity2__c, Customer_Project__c, etc. are present"
            ],
            sql_required=True,
            validation="All required FK columns present in staging"
        ))
        
        # Phase 2: Databricks
        self.steps.append(RemediationStep(
            phase=2,
            step=1,
            title="Audit Current Databricks Schema",
            description="Identify which dimension tables and columns are missing",
            commands=[
                "python Scripts/databricks_auditor.py audit-tables",
                "python Scripts/databricks_auditor.py audit-fk",
                "python Scripts/databricks_auditor.py generate-report"
            ],
            sql_required=False,
            estimated_time="5 min"
        ))
        
        self.steps.append(RemediationStep(
            phase=2,
            step=2,
            title="Generate Dimension Table DDL",
            description="Create SQL statements for missing dimension tables",
            commands=[
                "python Scripts/sfdc_cpq_remediation.py generate-ddl",
                "# Review and execute SQL in Databricks workspace"
            ],
            sql_required=True,
            estimated_time="15 min"
        ))
        
        self.steps.append(RemediationStep(
            phase=2,
            step=3,
            title="Materialize Dimension Tables",
            description="Execute DDL to create REP_SFDC_*_C dimension tables",
            commands=[
                "# Run generated SQL from step 2 in Databricks SQL Editor",
                "# Tables: OPPORTUNITY, ACCOUNT, CUSTOMER_PROJECT, INTAKE_FORM, BUILDING_CODE"
            ],
            sql_required=True,
            validation="All 5-7 dimension tables created successfully"
        ))
        
        self.steps.append(RemediationStep(
            phase=2,
            step=4,
            title="Validate Foreign Key Columns",
            description="Verify all FK columns exist and are populated in Quote table",
            commands=[
                "python Scripts/databricks_auditor.py audit-fk",
                "# Check that SBQQ__Opportunity2__c, etc. have > 0% population"
            ],
            sql_required=True,
            validation="FK columns present with >80% population rate"
        ))
        
        # Phase 3: YAML
        self.steps.append(RemediationStep(
            phase=3,
            step=1,
            title="Validate Current YAML",
            description="Check current relationship definitions in semabridge.yaml",
            commands=[
                "python Scripts/sfdc_cpq_remediation.py validate-yaml",
                f"# Review {self.config_path} relationships section"
            ],
            yaml_required=True,
            estimated_time="10 min"
        ))
        
        self.steps.append(RemediationStep(
            phase=3,
            step=2,
            title="Generate Corrected YAML",
            description="Create relationship definitions with proper join logic",
            commands=[
                "python Scripts/sfdc_cpq_remediation.py validate-yaml",
                "# Copy 'Generated Relationship YAML' section to clipboard"
            ],
            yaml_required=True,
            estimated_time="5 min"
        ))
        
        self.steps.append(RemediationStep(
            phase=3,
            step=3,
            title="Update semabridge.yaml",
            description="Replace relationships section with corrected definitions",
            commands=[
                f"# Open {self.config_path}",
                "# Replace 'relationships:' section with generated YAML",
                "# Ensure join_type, join_keys, and cardinality are correct"
            ],
            yaml_required=True,
            validation="YAML validates without errors"
        ))
        
        self.steps.append(RemediationStep(
            phase=3,
            step=4,
            title="Restart Semabridge API",
            description="Reload configuration and recompile semantic model",
            commands=[
                "# Stop running semabridge API",
                "uv run uvicorn semabridge.api.main:app --host 127.0.0.1 --port 8001",
                "# Monitor logs for 'Relationships: X compiled' message"
            ],
            estimated_time="2 min"
        ))
        
        self.steps.append(RemediationStep(
            phase=3,
            step=5,
            title="Validate Measure Views",
            description="Confirm all measure views compile and execute successfully",
            commands=[
                "# Check API logs for successful mv_* compilation",
                "# Test query via API: POST /api/sync with model validation",
                "# Verify zero relationship skipping warnings"
            ],
            sql_required=True,
            validation="No 'Skipping join key' or 'measure view failed' warnings"
        ))
    
    def print_overview(self):
        """Print high-level plan overview"""
        console.print("\n[bold cyan]Salesforce CPQ to Databricks Remediation Plan[/bold cyan]\n")
        
        for phase_num, phase_info in self.PHASES.items():
            status_color = {
                "NOT_STARTED": "red",
                "IN_PROGRESS": "yellow",
                "COMPLETE": "green"
            }[phase_info["status"]]
            
            console.print(f"[bold]{phase_num}. {phase_info['title']}[/bold]")
            console.print(f"   Status: [{status_color}]{phase_info['status']}[/{status_color}]")
            console.print(f"   {phase_info['description']}\n")
    
    def print_phase_details(self, phase: int):
        """Print detailed steps for specific phase"""
        if phase not in self.PHASES:
            console.print(f"[red]Invalid phase: {phase}[/red]")
            return
        
        phase_info = self.PHASES[phase]
        console.print(f"\n[bold cyan]{phase}. {phase_info['title']}[/bold cyan]\n")
        console.print(f"{phase_info['description']}\n")
        
        phase_steps = [s for s in self.steps if s.phase == phase]
        
        for step in phase_steps:
            console.print(f"[bold yellow]Step {step.phase}.{step.step}: {step.title}[/bold yellow]")
            console.print(f"  Description: {step.description}")
            console.print(f"  Estimated time: {step.estimated_time}")
            
            if step.commands:
                console.print(f"  [bold]Actions:[/bold]")
                for cmd in step.commands:
                    console.print(f"    {cmd}")
            
            if step.validation:
                console.print(f"  [bold green]✓ Validation:[/bold green] {step.validation}")
            
            console.print()


class SemabridgeConfigValidator:
    """Validate and report on semabridge.yaml configuration"""
    
    def __init__(self, config_path: str = "Config/semabridge.yaml"):
        self.config_path = Path(config_path)
        self.config = {}
        self.issues = []
        
        if self.config_path.exists():
            with open(self.config_path) as f:
                self.config = yaml.safe_load(f) or {}
    
    def validate(self) -> Dict:
        """Run full validation"""
        
        report = {
            "config_file": str(self.config_path),
            "exists": self.config_path.exists(),
            "issues": [],
            "statistics": {}
        }
        
        if not self.config_path.exists():
            report["issues"].append("Configuration file not found")
            return report
        
        # Check basic structure
        required_keys = ["project_name", "source", "targets"]
        for key in required_keys:
            if key not in self.config:
                report["issues"].append(f"Missing required key: {key}")
        
        # Count relationships
        relationships = self.config.get("relationships", {})
        report["statistics"]["relationship_count"] = len(relationships)
        
        # List defined relationships
        report["statistics"]["relationships"] = list(relationships.keys())
        
        # Check for common issues
        if "relationships" not in self.config:
            report["issues"].append("No relationships defined in configuration")
        
        return report


def cmd_plan_overview(args):
    """Display remediation plan overview"""
    plan = RemediationPlan()
    plan.print_overview()


def cmd_plan_phase(args):
    """Display detailed phase plan"""
    phase = getattr(args, 'phase', 1)
    plan = RemediationPlan()
    plan.print_phase_details(int(phase))


def cmd_validate_config(args):
    """Validate semabridge configuration"""
    console.print("\n[bold cyan]Semabridge Configuration Validation[/bold cyan]\n")
    
    validator = SemabridgeConfigValidator()
    report = validator.validate()
    
    console.print(f"Configuration file: [cyan]{report['config_file']}[/cyan]")
    console.print(f"Exists: [green]✓[/green]" if report['exists'] else f"Exists: [red]✗[/red]")
    
    stats = report['statistics']
    console.print(f"\nDefined relationships: {stats.get('relationship_count', 0)}")
    
    if stats.get('relationships'):
        console.print("\nRelationships:")
        for rel in stats['relationships']:
            console.print(f"  - {rel}")
    
    if report['issues']:
        console.print("\n[red]Issues found:[/red]")
        for issue in report['issues']:
            console.print(f"  ❌ {issue}")
    else:
        console.print("\n[green]✓ No validation issues found[/green]")


def cmd_checklist(args):
    """Display interactive remediation checklist"""
    console.print("\n[bold cyan]Remediation Checklist[/bold cyan]\n")
    
    plan = RemediationPlan()
    
    checklist_items = [
        ("Phase 1", "Review current ELT/replication configuration"),
        ("Phase 1", "Update extraction query to include all FK columns"),
        ("Phase 1", "Verify new columns exist in Databricks staging"),
        ("Phase 2", "Run audit to identify missing tables and columns"),
        ("Phase 2", "Generate DDL for missing dimension tables"),
        ("Phase 2", "Execute DDL in Databricks"),
        ("Phase 2", "Validate FK columns and population rates"),
        ("Phase 3", "Validate current YAML relationship definitions"),
        ("Phase 3", "Generate corrected relationship YAML"),
        ("Phase 3", "Update semabridge.yaml with new relationships"),
        ("Phase 3", "Restart Semabridge API"),
        ("Phase 3", "Test measure view compilation and validation"),
    ]
    
    table = Table(title="Remediation Checklist")
    table.add_column("☐", style="cyan")
    table.add_column("Phase")
    table.add_column("Task")
    
    for phase, task in checklist_items:
        table.add_row("☐", phase, task)
    
    if Table:
        console.print(table)
    else:
        for phase, task in checklist_items:
            console.print(f"☐ [{phase}] {task}")


def main():
    """CLI entry point"""
    
    import sys
    
    if len(sys.argv) < 2:
        console.print("""[bold]Semabridge Remediation Coordinator[/bold]

Usage:
  python semabridge_remediation_coordinator.py <command> [options]

Commands:
  plan-overview       Display overview of all 3 remediation phases
  plan-phase          Display detailed steps for specific phase (--phase N)
  validate-config     Validate current semabridge.yaml
  checklist           Display interactive remediation checklist

Examples:
  python semabridge_remediation_coordinator.py plan-overview
  python semabridge_remediation_coordinator.py plan-phase --phase 1
  python semabridge_remediation_coordinator.py validate-config
""")
        return
    
    cmd = sys.argv[1]
    
    class Args:
        phase = 1
    
    args = Args()
    
    # Parse options
    for i, arg in enumerate(sys.argv[2:]):
        if arg == "--phase" and i + 2 < len(sys.argv):
            args.phase = sys.argv[i + 3]
    
    commands = {
        "plan-overview": cmd_plan_overview,
        "plan-phase": cmd_plan_phase,
        "validate-config": cmd_validate_config,
        "checklist": cmd_checklist,
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
