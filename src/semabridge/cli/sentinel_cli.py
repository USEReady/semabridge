"""
Sentinel CLI Commands.

Provides the 'sentinel' command group for monitoring and fixing
Snowflake compilation errors.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm

from semabridge.core.settings import get_settings
from semabridge.core.sentinel.monitor import QueryMonitor
from semabridge.core.sentinel.analyzer import ErrorParser, IdentifierAnalyzer, AccessAnalyzer
from semabridge.core.sentinel.agent import SentinelAgent, AgentAction
from semabridge.core.sentinel.persistence import FailureStore

sentinel_app = typer.Typer()
console = Console()


@sentinel_app.command()
def watch(
    interval: int = typer.Option(30, "--interval", "-i", help="Polling interval in seconds"),
    auto_fix: bool = typer.Option(False, "--auto-fix", help="Automatically apply high-confidence fixes"),
):
    """
    Monitor Snowflake for compilation errors in real-time.
    """
    settings = get_settings()
    monitor = QueryMonitor(settings.snowflake)
    analyzer_id = IdentifierAnalyzer(settings.snowflake)
    analyzer_access = AccessAnalyzer(settings.snowflake)
    agent = SentinelAgent(settings.llm)
    
    console.print(Panel.fit(
        f"[bold]Sentinel Watch[/bold]\n"
        f"Monitoring {settings.snowflake.account} every {interval}s...",
        style="cyan"
    ))
    
    known_failures = set()
    
    try:
        while True:
            failures = monitor.poll_failures(window_minutes=5, limit=10)
            
            for f in failures:
                if f.query_id in known_failures:
                    continue
                known_failures.add(f.query_id)
                
                # Persist failure
                store = FailureStore()
                is_new = store.add_failure(f)
                
                # Check status if not new
                if not is_new:
                    status_info = store.get_failure_status(f.query_id)
                    if status_info and status_info["status"] == "FIXED":
                        continue  # Skip already fixed issues
                
                # Analyze failure
                ctx = ErrorParser.parse(f)
                console.print(f"\n[bold red]Error Detected:[/bold red] {f.error_code} - {f.error_message}")
                console.print(f"[dim]Query ID: {f.query_id}[/dim]")
                
                # Run deterministic analysis
                if f.is_invalid_identifier:
                    ctx = analyzer_id.analyze(ctx)
                elif f.is_object_not_found:
                    ctx = analyzer_access.analyze(ctx)
                
                if ctx.root_cause:
                    console.print(f"[yellow]Root Cause:[/yellow] {ctx.root_cause}")
                
                if ctx.suggested_fix:
                    console.print(f"[green]Suggested Fix:[/green] {ctx.suggested_fix}")
                else:
                    # Fallback to AI
                    console.print("[blue]Consulting AI Agent...[/blue]")
                    action, content = agent.suggest_fix(ctx)
                    
                    if action != AgentAction.NONE:
                        console.print(f"[magenta]AI Suggestion ({action.value}):[/magenta]\n{content}")
            
            time.sleep(interval)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Sentinel Watch stopped.[/yellow]")


@sentinel_app.command()
def diagnose(
    query_id: str = typer.Argument(..., help="Failed Query ID"),
):
    """
    Diagnose a specific failed query (dry-run).
    """
    settings = get_settings()
    console.print(f"Diagnosing query {query_id}...")
    
    # Try to get from local store first
    store = FailureStore()
    target_failure = store.get_failure(query_id)
    
    if not target_failure:
        # Fallback to polling history
        console.print(f"[dim]Query {query_id} not in local store. Polling Snowflake history...[/dim]")
        monitor = QueryMonitor(settings.snowflake)
        failures = monitor.poll_failures(window_minutes=240, limit=200)
        target_failure = next((f for f in failures if f.query_id == query_id), None)
    
    if not target_failure:
        console.print(f"[red]Could not find query {query_id} in recent history (last 60 mins).[/red]")
        return

    # Analyze
    ctx = ErrorParser.parse(target_failure)
    analyzer_id = IdentifierAnalyzer(settings.snowflake)
    analyzer_access = AccessAnalyzer(settings.snowflake)
    
    if target_failure.is_invalid_identifier:
        ctx = analyzer_id.analyze(ctx)
    elif target_failure.is_object_not_found:
        ctx = analyzer_access.analyze(ctx)
        
    if ctx.root_cause:
        console.print(f"[yellow]Root Cause:[/yellow] {ctx.root_cause}")

    # Logic to delegate to AI if no deterministic fix
    agent = SentinelAgent(settings.llm)
    
    if ctx.suggested_fix:
        console.print(f"[green]Deterministic Fix:[/green] {ctx.suggested_fix}")
    else:
        console.print("[blue]No deterministic fix found. Consulting AI Agent...[/blue]")
        action, content = agent.suggest_fix(ctx)
        console.print(f"[magenta]AI Suggestion ({action.value}):[/magenta]\n{content}")

@sentinel_app.command()
def fix(
    query_id: str = typer.Argument(..., help="Failed Query ID to fix"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Apply a fix for a specific query failure.
    """
    settings = get_settings()
    # We need to find the failure details. 
    # Since we don't have a persistent store of failures yet, we have to re-poll 
    # or this command only works if the failure is recent.
    # ideally we'd store failures in a local DB (semabridge.db).
    # For now, let's try to fetch it from history again.
    
    store = FailureStore()
    target_failure = store.get_failure(query_id)
    
    if not target_failure:
        # Fallback to polling
        console.print(f"[dim]Query {query_id} not in local store. Polling Snowflake history...[/dim]")
        monitor = QueryMonitor(settings.snowflake)
        failures = monitor.poll_failures(window_minutes=240, limit=200)
        target_failure = next((f for f in failures if f.query_id == query_id), None)
    
    if not target_failure:
        console.print(f"[red]Could not find query {query_id} in recent history (last 60 mins).[/red]")
        return
        
    # Check if already fixed
    status_info = store.get_failure_status(query_id)
    if status_info and status_info["status"] == "FIXED":
        console.print(f"[green]Query {query_id} is already marked as FIXED.[/green]")
        if not Confirm.ask("Do you want to apply a fix anyway?"):
            return

    # Analyze
    console.print(f"Analyzing failure for query {query_id}...")
    ctx = ErrorParser.parse(target_failure)
    
    analyzer_id = IdentifierAnalyzer(settings.snowflake)
    analyzer_access = AccessAnalyzer(settings.snowflake)
    
    if target_failure.is_invalid_identifier:
        ctx = analyzer_id.analyze(ctx)
    elif target_failure.is_object_not_found:
        ctx = analyzer_access.analyze(ctx)
        
    fix_sql = ctx.suggested_fix
    
    # If no deterministic fix, ask Agent
    if not fix_sql:
        agent = SentinelAgent(settings.llm)
        action, content = agent.suggest_fix(ctx)
        if action == AgentAction.SQL_FIX:
            fix_sql = content
        else:
            console.print(f"[yellow]Agent suggested: {action.value}[/yellow]")
            console.print(content)
            return

    if not fix_sql:
        console.print("[red]No fix found.[/red]")
        return
        
    console.print(f"\n[bold green]Proposed Fix:[/bold green]")
    console.print(f"```sql\n{fix_sql}\n```")
    
    if not yes:
        if not Confirm.ask("Apply this fix?"):
            console.print("Cancelled.")
            return
            
    # Apply Fix
    try:
        with monitor.connection() as conn:
            cur = conn.cursor()
            # Split by semicolon to handle multiple statements
            stmts = [s.strip() for s in fix_sql.split(';') if s.strip()]
            for stmt in stmts:
                console.print(f"Executing: {stmt}")
                cur.execute(stmt)
        console.print("[green]Fix applied successfully![/green]")
        
        # Update status in store
        store.update_status(query_id, "FIXED", fix_query=fix_sql)
        console.print("[dim]Failure marked as FIXED in local store.[/dim]")
    except Exception as e:
        console.print(f"[red]Failed to apply fix: {e}[/red]")


@sentinel_app.command()
def snapshot(
    output: Path = typer.Option(Path("baseline.json"), "--output", "-o", help="Baseline output path"),
):
    """
    Take a snapshot of the current Snowflake schema state.
    """
    from semabridge.core.sentinel.governance import DriftDetector
    settings = get_settings()
    detector = DriftDetector(settings.snowflake)
    
    detector.take_snapshot(output)
    console.print(f"[green]Snapshot saved to {output}[/green]")


@sentinel_app.command()
def check_drift(
    baseline: Path = typer.Option(Path("baseline.json"), "--baseline", "-b", help="Baseline snapshot path"),
):
    """
    Check for schema drift against a baseline.
    """
    from semabridge.core.sentinel.governance import DriftDetector
    settings = get_settings()
    detector = DriftDetector(settings.snowflake)
    
    try:
        report = detector.check_drift(baseline_path=baseline)
        
        console.print(Panel.fit(
            f"[bold]Schema Drift Report[/bold]\n"
            f"Baseline: {baseline}",
            title="Drift Check"
        ))
        
        if not report.has_drift:
            console.print("[green]No drift detected.[/green]")
            return
            
        if report.breaking_changes:
            console.print("\n[bold red]Breaking Changes:[/bold red]")
            for change in report.breaking_changes:
                console.print(f"  - {change}")
                
        if report.non_breaking_changes:
            console.print("\n[bold yellow]Non-Breaking Changes:[/bold yellow]")
            for change in report.non_breaking_changes:
                console.print(f"  - {change}")
                
        if report.is_breaking:
            console.print("\n[red]Drift check failed due to breaking changes.[/red]")
            raise typer.Exit(code=1)
            
    except Exception as e:
        console.print(f"[red]Error checking drift: {e}[/red]")
        raise typer.Exit(code=1)


@sentinel_app.command()
def audit():
    """
    Audit Snowflake permissions and role configuration.
    """
    settings = get_settings()
    console.print(Panel.fit(
        f"[bold]RBAC Audit[/bold]\nRole: {settings.snowflake.role}",
        title="Audit"
    ))
    
    analyzer = AccessAnalyzer(settings.snowflake)
    try:
        warnings = analyzer.check_permissions()
        
        if not warnings:
            console.print("[green]✔ RBAC configuration looks good.[/green]")
        else:
            console.print("[red]✘ Permission issues detected:[/red]")
            for w in warnings:
                console.print(f"  - {w}")
            raise typer.Exit(code=1)
            
    except Exception as e:
        console.print(f"[red]Error during audit: {e}[/red]")
        raise typer.Exit(code=1)


@sentinel_app.command()
def validate(
    baseline: Optional[Path] = typer.Option(None, "--baseline", "-b", help="Baseline snapshot for drift check"),
):
    """
    Run pre-flight validation (Connectivity, RBAC, Drift).
    """
    console.print(Panel.fit("[bold]Sentinel Pre-Flight Check[/bold]", style="cyan"))
    
    # 1. Connectivity (implicitly checked by audit)
    # 2. RBAC Audit
    console.print("\n[bold]1. RBAC Audit[/bold]")
    try:
        audit()
    except typer.Exit as e:
        if e.exit_code != 0:
            console.print("[red]RBAC Audit failed. Fix permissions first.[/red]")
            raise
            
    # 3. Drift Check (if baseline provided)
    if baseline and baseline.exists():
        console.print(f"\n[bold]2. Schema Drift Check (vs {baseline})[/bold]")
        try:
            check_drift(baseline=baseline)
        except typer.Exit as e:
            if e.exit_code != 0:
                console.print("[red]Drift Check failed.[/red]")
                raise
    else:
        console.print("\n[dim]Skipping Drift Check (no baseline provided)[/dim]")
        
    console.print("\n[bold green]✔ All Systems Go[/bold green]")
