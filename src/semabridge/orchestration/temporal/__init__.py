"""
Temporal workflow engine for Semabridge.

Modules:
    workflows  – durable workflow definitions (extract → diff → emit)
    activities – idempotent activity implementations
    worker     – Temporal worker bootstrap and task-queue registration
"""
