"""YAML formatting helpers for Snowflake emitter artifacts."""

from __future__ import annotations

import yaml


class IndentDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super(IndentDumper, self).increase_indent(flow, False)


def str_presenter(dumper, data):
    if len(data.splitlines()) > 1 or len(data) > 80:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


IndentDumper.add_representer(str, str_presenter)
