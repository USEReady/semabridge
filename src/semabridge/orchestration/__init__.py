"""
Orchestration package for distributed Semabridge pipelines.

Provides Temporal-based workflow orchestration and an adapter shim
that allows the system to operate either locally (legacy SyncOrchestrator)
or in distributed mode (Temporal workers).
"""
