"""Shared helpers for the ontology test scripts.

Every script prints PASS / FAIL / WARN lines and returns an exit code
(0 = all PASS, 1 = at least one FAIL). Scripts never hard-code model names;
project ids and OSI files come from the command line or the API.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Install requests first:  uv pip install requests")

DEFAULT_BASE = os.environ.get("SEMABRIDGE_API", "http://127.0.0.1:8000")


class Report:
    def __init__(self, title: str):
        self.title = title
        self.rows: List[Tuple[str, str, str]] = []
        print(f"\n=== {title} ===")

    def _add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        line = f"  [{status}] {name}"
        if detail:
            line += f"  ->  {detail}"
        print(line)

    def ok(self, name: str, detail: str = "") -> None:
        self._add("PASS", name, detail)

    def fail(self, name: str, detail: str = "") -> None:
        self._add("FAIL", name, detail)

    def warn(self, name: str, detail: str = "") -> None:
        self._add("WARN", name, detail)

    def check(self, cond: bool, name: str, detail: str = "") -> bool:
        (self.ok if cond else self.fail)(name, detail)
        return cond

    def done(self) -> int:
        n_fail = sum(1 for r in self.rows if r[0] == "FAIL")
        n_pass = sum(1 for r in self.rows if r[0] == "PASS")
        n_warn = sum(1 for r in self.rows if r[0] == "WARN")
        print(f"--- {self.title}: {n_pass} pass, {n_fail} fail, {n_warn} warn")
        return 1 if n_fail else 0


def base_args(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--base", default=DEFAULT_BASE, help="API base URL (default %(default)s or $SEMABRIDGE_API)")
    p.add_argument("--token", default=os.environ.get("SEMABRIDGE_TOKEN"), help="Bearer token if auth is on")
    return p


class Api:
    def __init__(self, base: str, token: Optional[str] = None):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        if any(h in self.base for h in ("127.0.0.1", "localhost")):
            self.s.trust_env = False  # never send local calls through a corporate/HTTP proxy
        if not token:
            token = self._auto_login()
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"

    def _auto_login(self) -> Optional[str]:
        """Same as the UI: POST /auth/auto-login (harmless when AUTH_ENABLED=false)."""
        try:
            # separate one-off request: a failed login must not poison the session's connection pool
            r = requests.post(self.url("/auth/auto-login"), timeout=30,
                              proxies={"http": None, "https": None} if not self.s.trust_env else None)
            if r.ok:
                return r.json().get("access_token")
        except Exception:
            pass
        return None

    def url(self, path: str) -> str:
        return self.base + path

    def get(self, path: str, **kw) -> requests.Response:
        return self.s.get(self.url(path), timeout=120, **kw)

    def post(self, path: str, body: Any = None, **kw) -> requests.Response:
        return self.s.post(self.url(path), json=body, timeout=300, **kw)

    def put(self, path: str, body: Any = None, **kw) -> requests.Response:
        return self.s.put(self.url(path), json=body, timeout=120, **kw)

    def delete(self, path: str, **kw) -> requests.Response:
        return self.s.delete(self.url(path), timeout=60, **kw)

    def upload(self, files: List[Tuple[str, bytes]]) -> requests.Response:
        multipart = [("files", (name, data, "application/x-yaml")) for name, data in files]
        return self.s.post(self.url("/api/ontology/domains/upload"), files=multipart, timeout=120)

    def delete_domain_quiet(self, name: str) -> None:
        try:
            self.delete(f"/api/ontology/domains/{requests.utils.quote(name, safe='')}")
        except Exception:
            pass


def body_text(r: requests.Response) -> str:
    try:
        return json.dumps(r.json())[:400]
    except Exception:
        return r.text[:400]


def unique(prefix: str) -> str:
    """Unique, safe test-domain name so tests never touch real domains."""
    return f"{prefix} {uuid.uuid4().hex[:6]}"


def find_osi_file(repo: Path, project_id: str) -> Optional[Path]:
    """The deploy step writes output/inferred/<project_id>/osi_inferred.json (OSIModel.model_dump)."""
    cands = [
        repo / "output" / "inferred" / project_id / "osi_inferred.json",
        *sorted((repo / "output" / "inferred").glob(f"{project_id}*/osi_inferred.json")),
    ]
    for c in cands:
        if c.exists():
            return c
    return None


# ---------------------------------------------------------------- governance helpers
def make_domain_yaml(name: str, concepts=None, metrics=None, relationships=None, owner=None) -> str:
    """Build a domain YAML from plain dicts (keeps tests readable, no model names)."""
    doc = {"domain": name}
    if owner:
        doc["owner"] = owner
    if concepts is not None:
        doc["concepts"] = concepts
    if metrics is not None:
        doc["metrics"] = metrics
    if relationships is not None:
        doc["relationships"] = relationships
    import yaml
    return yaml.safe_dump(doc, sort_keys=False)


def save_domain(api: "Api", yaml_text: str):
    return api.post("/api/ontology/domains", {"yaml_content": yaml_text})


def issues_for(report: dict, domains) -> list:
    """Lint issues that mention any of our test domains (the store also holds other domains)."""
    names = [d.casefold() for d in domains]
    out = []
    for i in report.get("issues", []):
        blob = f"{i.get('domain') or ''} {i.get('message') or ''} {i.get('object_name') or ''}".casefold()
        if any(n in blob for n in names):
            out.append(i)
    return out
