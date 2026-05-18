"""Compatibility shim for legacy TMSL transformer imports."""

from __future__ import annotations

import warnings

warnings.warn(
    "semabridge.converter.tmsl_transformer is deprecated; use "
    "semabridge.converter.tmdl_transformer instead.",
    DeprecationWarning,
    stacklevel=2,
)

from semabridge.converter.tmdl_transformer import *  # noqa: F401,F403

