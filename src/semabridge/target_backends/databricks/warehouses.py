"""SQL warehouse discovery and choice (CS-10).

Uses the SQL Warehouses REST API (GET /api/2.0/sql/warehouses and
GET /api/2.0/sql/warehouses/{id}); reading never starts a warehouse.

Choice when no warehouse is configured: RUNNING first, then STARTING, then
STOPPED (then STOPPING) -- the least cold-start wait first; within a state,
serverless (PRO + enable_serverless_compute) before classic, then by name.
DELETING / DELETED warehouses are never chosen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_STATE_RANK = {"RUNNING": 0, "STARTING": 1, "STOPPED": 2, "STOPPING": 3}


@dataclass(frozen=True)
class Warehouse:
    id: str
    name: str
    state: str
    serverless: bool
    warehouse_type: str = ""
    cluster_size: str = ""

    @property
    def usable(self) -> bool:
        return self.state in _STATE_RANK

    @property
    def needs_start(self) -> bool:
        """A query would have to wait for (or trigger) a start."""
        return self.state != "RUNNING"

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Warehouse":
        wtype = str(data.get("warehouse_type") or "")
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            state=str(data.get("state") or "").upper(),
            serverless=bool(data.get("enable_serverless_compute")) and wtype.upper() in ("PRO", ""),
            warehouse_type=wtype,
            cluster_size=str(data.get("cluster_size") or ""),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "state": self.state, "serverless": self.serverless,
                "warehouse_type": self.warehouse_type, "cluster_size": self.cluster_size}


def rank(warehouse: Warehouse) -> tuple:
    return (_STATE_RANK.get(warehouse.state, 99), 0 if warehouse.serverless else 1, warehouse.name.lower(), warehouse.id)


def choose_warehouse(warehouses: List[Warehouse]) -> Optional[Warehouse]:
    usable = [w for w in warehouses if w.usable and w.id]
    return sorted(usable, key=rank)[0] if usable else None


def list_warehouses(client: Any) -> List[Warehouse]:
    """All warehouses the caller can see (follows page tokens)."""
    out: List[Warehouse] = []
    params: Dict[str, Any] = {}
    for _ in range(50):  # page cap
        body = client.get("/api/2.0/sql/warehouses", params=params or None) or {}
        out.extend(Warehouse.from_api(w) for w in body.get("warehouses") or [])
        token = body.get("next_page_token")
        if not token:
            break
        params = {"page_token": token}
    return out


def get_warehouse(client: Any, warehouse_id: str) -> Warehouse:
    return Warehouse.from_api(client.get(f"/api/2.0/sql/warehouses/{warehouse_id}") or {})


def resolve_warehouse(client: Any, configured_id: Optional[str] = None) -> Dict[str, Any]:
    """The warehouse to use: the configured one if it exists, else the best
    discovered one. Returns {"warehouse": Warehouse|None, "source": ..., "message": ...}."""
    if configured_id:
        warehouse = get_warehouse(client, configured_id)
        if warehouse.usable:
            return {"warehouse": warehouse, "source": "configured", "message": f"Using configured warehouse {warehouse.name}"}
        return {"warehouse": None, "source": "configured",
                "message": f"Configured warehouse {configured_id} is {warehouse.state or 'not found'}"}
    chosen = choose_warehouse(list_warehouses(client))
    if chosen is None:
        return {"warehouse": None, "source": "discovered", "message": "No usable SQL warehouse is visible to this identity"}
    return {"warehouse": chosen, "source": "discovered",
            "message": f"Auto-selected {chosen.name} ({chosen.state}{', serverless' if chosen.serverless else ''})"}
