"""The exact statements a deploy may send (Phase 3.8).

Every mutating statement is generated here, from the approved bundle only.
The run's guard WritePlan carries the hash of each exact text, so nothing else
-- not even a near-identical statement -- can be sent under that plan.

Forms (and only these):
* stub table   -- CREATE TABLE <fqn> (<col> <TYPE>, ...) USING DELTA
                  TBLPROPERTIES ('semabridge.stub' = 'true', ...)
                  managed Delta, plain CREATE (the name is verified absent first);
* new view     -- CREATE VIEW <fqn> WITH METRICS LANGUAGE YAML AS $$<body>$$
* staging view -- the same, under <view>__sbstg_<token>
* update view  -- ALTER VIEW <fqn> AS $$<body>$$   (documented to keep grants)
* drop staging -- DROP VIEW <staging fqn>
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from semabridge.target_backends.databricks.writer import quote_three_part

STAGING_MARKER = "__sbstg_"

STUB_TABLE = "create_stub_table"
CREATE_VIEW = "create_view"
CREATE_STAGING = "create_staging_view"
ALTER_VIEW = "alter_view"
DROP_STAGING = "drop_staging_view"
KINDS = (STUB_TABLE, CREATE_VIEW, CREATE_STAGING, ALTER_VIEW, DROP_STAGING)

# Spark SQL types an empty stub column may have (what 3.4 produces).
_TYPE = re.compile(
    r"(STRING|BIGINT|INT|SMALLINT|TINYINT|DOUBLE|FLOAT|BOOLEAN|DATE|TIMESTAMP|TIMESTAMP_NTZ|BINARY|"
    r"DECIMAL(\(\s*\d{1,2}\s*,\s*\d{1,2}\s*\))?)", re.IGNORECASE)
_PROP_KEY = re.compile(r"semabridge\.[a-z_]+")
_PROP_VALUE = re.compile(r"[A-Za-z0-9_.:\-]{0,128}")


class StatementError(ValueError):
    """A statement could not be built safely (bad type, property or body)."""


def statement_hash(sql: str) -> str:
    """Hash of the exact text as sent (only surrounding whitespace and one final
    ';' are ignored)."""
    text = str(sql).strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Statement:
    kind: str
    target: str                    # normalized catalog.schema.name
    sql: str
    view: Optional[str] = None     # the bundle view it serves (None for stub tables)
    body_sha256: Optional[str] = None

    @property
    def sha256(self) -> str:
        return statement_hash(self.sql)

    def as_dict(self) -> Dict[str, object]:
        return {"kind": self.kind, "target": self.target, "view": self.view, "sql": self.sql,
                "sha256": self.sha256}


def _fqn(catalog: str, schema: str, name: str) -> Tuple[str, str]:
    from semabridge.target_backends.databricks.guard import normalize_name

    quoted = quote_three_part(catalog, schema, name)
    return quoted, normalize_name(quoted)


def _body(yaml_text: str) -> str:
    if "$$" in yaml_text:
        raise StatementError("the definition contains '$$'")
    return yaml_text


def staging_name(view: str, token: str) -> str:
    if not re.fullmatch(r"[a-z0-9]{6,16}", token or ""):
        raise StatementError("staging token must be 6-16 lowercase letters/digits")
    return f"{view}{STAGING_MARKER}{token}"


def stub_table(catalog: str, schema: str, name: str, columns: Sequence[Tuple[str, str]],
               properties: Dict[str, str]) -> Statement:
    from semabridge.target_backends.databricks.writer import quote_identifier

    if not columns:
        raise StatementError(f"stub table {name} has no columns")
    seen = set()
    cols: List[str] = []
    for col, typ in columns:
        t = str(typ or "").strip()
        if not _TYPE.fullmatch(t):
            raise StatementError(f"stub column {name}.{col}: type {typ!r} is not an approved Spark type")
        if not str(col).strip() or str(col).lower() in seen:
            raise StatementError(f"stub table {name}: empty or duplicate column {col!r}")
        seen.add(str(col).lower())
        cols.append(f"  {quote_identifier(col)} {t.upper().replace(' ', '')}")
    props = {"semabridge.stub": "true", **{k: v for k, v in properties.items() if k != "semabridge.stub"}}
    rendered: List[str] = []
    for k in sorted(props):
        v = str(props[k])
        if not _PROP_KEY.fullmatch(k) or not _PROP_VALUE.fullmatch(v):
            raise StatementError(f"stub table property {k!r}={v!r} is not allowed")
        rendered.append(f"'{k}' = '{v}'")
    quoted, norm = _fqn(catalog, schema, name)
    sql = (f"CREATE TABLE {quoted} (\n" + ",\n".join(cols) + "\n) USING DELTA\nTBLPROPERTIES ("
           + ", ".join(rendered) + ")")
    return Statement(STUB_TABLE, norm, sql)


def create_view(catalog: str, schema: str, view: str, yaml_text: str, body_sha256: str, *,
                staging_token: Optional[str] = None) -> Statement:
    name = staging_name(view, staging_token) if staging_token else view
    quoted, norm = _fqn(catalog, schema, name)
    sql = f"CREATE VIEW {quoted} WITH METRICS LANGUAGE YAML AS $$\n{_body(yaml_text)}$$"
    return Statement(CREATE_STAGING if staging_token else CREATE_VIEW, norm, sql, view=view, body_sha256=body_sha256)


def alter_view(catalog: str, schema: str, view: str, yaml_text: str, body_sha256: str) -> Statement:
    quoted, norm = _fqn(catalog, schema, view)
    return Statement(ALTER_VIEW, norm, f"ALTER VIEW {quoted} AS $$\n{_body(yaml_text)}$$", view=view,
                     body_sha256=body_sha256)


def drop_staging(catalog: str, schema: str, view: str, staging_token: str) -> Statement:
    quoted, norm = _fqn(catalog, schema, staging_name(view, staging_token))
    return Statement(DROP_STAGING, norm, f"DROP VIEW {quoted}", view=view)


def write_plan(statements: Iterable[Statement]):
    """The guard WritePlan for exactly these statements."""
    from semabridge.target_backends.databricks.guard import WritePlan

    return WritePlan.for_deploy(statements)
