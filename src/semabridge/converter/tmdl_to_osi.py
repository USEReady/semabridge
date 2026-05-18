"""Primary TMDL-to-OSI import surface.

This module provides the TMDL-first converter names while delegating to the
established implementation in ``tmsl_to_osi`` for runtime compatibility.
"""

from __future__ import annotations

from semabridge.converter.tmsl_to_osi import (  # noqa: F401
    TMDLToOSIConverter,
    TMSLToOSIConverter,
)

__all__ = ["TMDLToOSIConverter", "TMSLToOSIConverter"]

