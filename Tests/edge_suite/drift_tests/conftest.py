"""Shared helpers. Every name used in a test is built here, never a real one."""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from drift_tests import adapter  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

M = adapter.models
DIALECTS = ["snowflake", "databricks"]


def read_fixture(platform: str, name: str) -> str:
    return (FIXTURES / platform / name).read_text(encoding="utf-8")


def obj(kind, name, expression="", description="", table="T1"):
    return M.ObjectDef(kind=kind, table=table, name=name,
                       expression=expression, description=description)


def view(objects, *, source_path="ddl", platform="snowflake", capabilities=None,
         not_deployed=None, view_name="DB1.SCH1.VIEW_ONE"):
    kwargs = dict(view_name=view_name, platform=platform, source_path=source_path,
                  objects=list(objects))
    if capabilities is not None:
        kwargs["capabilities"] = capabilities
    if not_deployed is not None:
        kwargs["not_deployed"] = list(not_deployed)
    return M.ViewDefinition(**kwargs)


def categories(findings):
    return sorted(f.category for f in findings)


@pytest.fixture(params=DIALECTS)
def dialect(request):
    """Every normalisation rule must hold for every supported dialect."""
    return request.param
