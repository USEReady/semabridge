import time
import sys

modules_to_test = [
    "semabridge.api.account_router",
    "semabridge.api.app_setup",
    "semabridge.api.browse",
    "semabridge.api.discovery_api",
    "semabridge.api.repo_router",
    "semabridge.api.routers.connection_router",
    "semabridge.api.routers.core_router",
    "semabridge.api.routers.project_router",
    "semabridge.api.settings_api",
    "semabridge.api.llm_provider_router",
    "semabridge.api.sync_router",
    "semabridge.api.ui",
    "semabridge.api.websocket_alerts",
    "semabridge.api.routers.diagnostics_router",
    "semabridge.api.routers.mapping_router",
    "semabridge.api.routers.naming_router",
    "semabridge.api.routers.synonyms_router",
    "semabridge.api.routers.domain_ontology_router",
    "semabridge.api.routers.ai_router",
    "semabridge.api.routers.reconciliation_router",
    "semabridge.api.routers.governance_router",
    "semabridge.api.routers.fabric_cloud_router",
    "semabridge.api.routers.security_router",
    "semabridge.api.routers.databricks_metric_router",
    "semabridge.api.routers.maintenance_router",
    "semabridge.api.routers.drift_router",
    "semabridge.api.routers.reverse_sync_router",
    "semabridge.api.routers.bi_truth_router",
    "semabridge.api.routers.cross_platform_router",
]

total_t0 = time.perf_counter()
for mod in modules_to_test:
    t0 = time.perf_counter()
    __import__(mod)
    dt = time.perf_counter() - t0
    if dt > 0.1:
        print(f"SLOW IMPORT: {mod:<50} took {dt:.3f}s")
    else:
        print(f"FAST:        {mod:<50} took {dt:.3f}s")

print(f"\nTotal import time: {time.perf_counter() - total_t0:.3f}s")
