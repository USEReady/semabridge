#!/usr/bin/env python3
"""
Quick Diagnostic Summary

Runs all major diagnostics and provides an executive summary of remediation status.
Good starting point before diving into phase-by-phase remediation.
"""

import subprocess
import json
from pathlib import Path
from typing import Dict, List
import yaml

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
except ImportError:
    class Console:
        def print(self, *args, **kwargs): print(*args)
    Panel = None
    Text = None
    Table = None

console = Console()


class QuickDiagnostic:
    """Run quick diagnostics and summarize findings"""
    
    def __init__(self):
        self.findings = {
            "phase1": {"complete": False, "issues": []},
            "phase2": {"complete": False, "issues": []},
            "phase3": {"complete": False, "issues": []},
        }
        self.recommendations = []
    
    def check_elt_status(self) -> Dict:
        """Check Phase 1 ELT status"""
        console.print("[cyan]Checking Phase 1: ELT Pipeline...[/cyan]", end=" ")
        
        result = {
            "complete": False,
            "issues": [],
            "details": {}
        }
        
        # Try to read from Databricks or config
        config_path = Path("Config/semabridge.yaml")
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            
            # Check if mappings include FK columns
            mappings = config.get("mappings", [])
            if mappings:
                sample_mapping = mappings[0] if mappings else {}
                columns = [c.get("source") for c in sample_mapping.get("columns", [])]
                
                required_fks = [
                    "SBQQ__Opportunity2__c",
                    "SBQQ__Account__c",
                    "Customer_Project__c",
                ]
                
                missing_fks = [fk for fk in required_fks if fk not in columns]
                
                if missing_fks:
                    result["issues"].append(f"Missing FK columns in mapping: {missing_fks}")
                    result["details"]["missing_columns"] = missing_fks
                else:
                    result["complete"] = True
        else:
            result["issues"].append("No semabridge.yaml found")
        
        status = "[green]✓[/green]" if result["complete"] else "[red]✗[/red]"
        console.print(status)
        return result
    
    def check_databricks_status(self) -> Dict:
        """Check Phase 2 Databricks status"""
        console.print("[cyan]Checking Phase 2: Databricks Tables...[/cyan]", end=" ")
        
        result = {
            "complete": False,
            "issues": [],
            "details": {}
        }
        
        # Check for existence of dimension tables
        required_tables = [
            "REP_SFDC_SBQQ__QUOTE__C",
            "REP_SFDC_OPPORTUNITY_C",
            "REP_SFDC_ACCOUNT_C",
            "REP_SFDC_CUSTOMER_PROJECT_C",
            "REP_SFDC_INTAKE_FORM_C",
            "REP_SFDC_BUILDING_CODE_C",
        ]
        
        try:
            from pyspark.sql import SparkSession
            spark = SparkSession.builder.getOrCreate()
            
            existing_tables = []
            missing_tables = []
            
            for table in required_tables:
                try:
                    spark.sql(f"SELECT 1 FROM semabridge.public.{table} LIMIT 1")
                    existing_tables.append(table)
                except:
                    missing_tables.append(table)
            
            result["details"]["existing_tables"] = existing_tables
            result["details"]["missing_tables"] = missing_tables
            
            if missing_tables:
                result["issues"].append(f"Missing tables: {missing_tables}")
            elif existing_tables == required_tables:
                result["complete"] = True
        
        except Exception as e:
            result["issues"].append(f"Could not access Databricks: {str(e)}")
        
        status = "[green]✓[/green]" if result["complete"] else "[red]✗[/red]"
        console.print(status)
        return result
    
    def check_yaml_status(self) -> Dict:
        """Check Phase 3 YAML status"""
        console.print("[cyan]Checking Phase 3: YAML Configuration...[/cyan]", end=" ")
        
        result = {
            "complete": False,
            "issues": [],
            "details": {}
        }
        
        config_path = Path("Config/semabridge.yaml")
        if not config_path.exists():
            result["issues"].append("semabridge.yaml not found")
            status = "[red]✗[/red]"
            console.print(status)
            return result
        
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        
        relationships = config.get("relationships", {})
        result["details"]["relationship_count"] = len(relationships)
        
        # Check for required relationships
        required_rels = [
            "REL_QUOTE_TO_OPPORTUNITY",
            "REL_QUOTE_TO_ACCOUNT",
            "REL_QUOTE_TO_CUSTOMER_PROJECT",
        ]
        
        missing_rels = [rel for rel in required_rels if rel not in relationships]
        
        if missing_rels:
            result["issues"].append(f"Missing required relationships: {missing_rels}")
        elif relationships:
            result["complete"] = True
        else:
            result["issues"].append("No relationships defined in YAML")
        
        status = "[green]✓[/green]" if result["complete"] else "[red]✗[/red]"
        console.print(status)
        return result
    
    def run_diagnostic(self):
        """Run all diagnostics"""
        console.print("\n[bold cyan]═══════════════════════════════════════════════════════[/bold cyan]")
        console.print("[bold cyan]  Semabridge CPQ Integration Quick Diagnostic[/bold cyan]")
        console.print("[bold cyan]═══════════════════════════════════════════════════════[/bold cyan]\n")
        
        # Run all checks
        phase1 = self.check_elt_status()
        phase2 = self.check_databricks_status()
        phase3 = self.check_yaml_status()
        
        self.findings["phase1"] = phase1
        self.findings["phase2"] = phase2
        self.findings["phase3"] = phase3
        
        # Calculate overall status
        phases_complete = sum(1 for p in [phase1, phase2, phase3] if p["complete"])
        
        console.print(f"\n[bold]Status: {phases_complete}/3 phases complete[/bold]\n")
        
        # Build recommendations
        self._build_recommendations()
        
        # Display findings
        self._display_findings()
        
        # Display recommendations
        self._display_recommendations()
    
    def _build_recommendations(self):
        """Build remediation recommendations"""
        if not self.findings["phase1"]["complete"]:
            self.recommendations.append(
                "[bold yellow]1. [Phase 1 - ELT Pipeline][/bold yellow]\n"
                "   Execute: python Scripts/sfdc_cpq_remediation.py generate-elt\n"
                "   Then: Update your Salesforce extraction query to include FK columns\n"
                "   Timeline: 20-30 minutes"
            )
        
        if not self.findings["phase2"]["complete"]:
            phase2_rec = "[bold yellow]2. [Phase 2 - Databricks][/bold yellow]\n"
            phase2_rec += "   Execute: python Scripts/sfdc_cpq_remediation.py generate-ddl\n"
            phase2_rec += "   Then: Run generated SQL in Databricks to create dimension tables\n"
            
            missing = self.findings["phase2"]["details"].get("missing_tables", [])
            if missing:
                phase2_rec += f"   Missing: {', '.join(missing)}\n"
            
            phase2_rec += "   Timeline: 20-30 minutes"
            self.recommendations.append(phase2_rec)
        
        if not self.findings["phase3"]["complete"]:
            self.recommendations.append(
                "[bold yellow]3. [Phase 3 - YAML Configuration][/bold yellow]\n"
                "   Execute: python Scripts/sfdc_cpq_remediation.py validate-yaml\n"
                "   Then: Update Config/semabridge.yaml with corrected relationships\n"
                "   Restart: uv run uvicorn semabridge.api.main:app\n"
                "   Timeline: 15-20 minutes"
            )
    
    def _display_findings(self):
        """Display detailed findings"""
        console.print("[bold cyan]Detailed Findings:[/bold cyan]\n")
        
        for phase, data in self.findings.items():
            phase_num = {"phase1": "1", "phase2": "2", "phase3": "3"}[phase]
            phase_title = {
                "phase1": "ELT Pipeline Schema Extraction",
                "phase2": "Databricks Lakehouse Tables",
                "phase3": "Semantic YAML Relationships"
            }[phase]
            
            status = "[green]✓ Complete[/green]" if data["complete"] else "[red]✗ Incomplete[/red]"
            
            console.print(f"[bold]Phase {phase_num}: {phase_title}[/bold]")
            console.print(f"Status: {status}")
            
            if data["issues"]:
                for issue in data["issues"]:
                    console.print(f"  ⚠ {issue}")
            
            if data["details"]:
                for key, value in data["details"].items():
                    if isinstance(value, list):
                        if len(value) <= 5:
                            console.print(f"  {key}: {value}")
                        else:
                            console.print(f"  {key}: {len(value)} items")
                    else:
                        console.print(f"  {key}: {value}")
            
            console.print()
    
    def _display_recommendations(self):
        """Display remediation recommendations"""
        if not self.recommendations:
            console.print("[bold green]✓ All phases complete! No remediation needed.[/bold green]")
            return
        
        console.print("[bold cyan]Recommended Next Steps:[/bold cyan]\n")
        
        for rec in self.recommendations:
            console.print(rec)
            console.print()
        
        console.print("[bold cyan]Additional Resources:[/bold cyan]")
        console.print("  • Full guide: Scripts/REMEDIATION_GUIDE.md")
        console.print("  • All phases: python Scripts/semabridge_remediation_coordinator.py plan-overview")
        console.print("  • Interactive: python Scripts/semabridge_remediation_coordinator.py checklist")


def main():
    """Main entry point"""
    import sys
    
    diagnostic = QuickDiagnostic()
    
    try:
        diagnostic.run_diagnostic()
    except Exception as e:
        console.print(f"\n[red]Error during diagnostic: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
