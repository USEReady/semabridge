"""Helper to load Microsoft Tabular Object Model (TOM) via pythonnet.

Usage:
1. Install pythonnet in your virtualenv: `pip install pythonnet`
2. Obtain the TOM assembly (e.g. from the NuGet package `Microsoft.AnalysisServices.Tabular` or your Analysis Services install).
   Place the DLL somewhere accessible, e.g. `libs/Microsoft.AnalysisServices.Tabular.dll`.
3. From Python, call `load_tom_and_inspect(dll_path)` or `try_parse_tmsl(dll_path, tmsl_path)`.

Notes:
- This script does not bundle TOM—you must provide the matching assembly for your runtime.
- The script uses reflection to find candidate types/methods for parsing TMSL/TMDL and will attempt a best-effort call.
"""

from pathlib import Path
import sys

try:
    import clr
except Exception as exc:
    raise RuntimeError("pythonnet is required. Install with: pip install pythonnet") from exc

import System
from System import Array
from System.Reflection import Assembly


def load_assembly(dll_path: str) -> Assembly:
    """Load a .NET assembly from a path and return the Assembly instance."""
    p = Path(dll_path)
    if not p.exists():
        raise FileNotFoundError(f"Assembly not found: {dll_path}")
    # Use System.Reflection to load the file so we can inspect types
    asm = Assembly.LoadFrom(str(p.resolve()))
    return asm


def inspect_assembly(asm: Assembly) -> dict:
    """Return a summary dict of types and candidate parsing methods.

    The result includes types containing 'Tabular' or 'Model' and lists of method names.
    """
    types = list(asm.GetTypes())
    summary = {}
    for t in types:
        name = t.FullName
        if ("Tabular" in name) or ("Model" in name) or ("Serializer" in name):
            methods = [m.Name for m in t.GetMethods()]
            summary[name] = methods
    return summary


def try_parse_tmsl(dll_path: str, tmsl_path: str) -> dict:
    """Attempt to parse a TMSL/TMDL file using the loaded TOM assembly.

    This is a best-effort function: it will look for candidate types and methods
    that might accept a string or stream input (e.g. Deserialize, Parse, LoadFromXml)
    and try to invoke them. It returns a report with success/failure and any
    returned object info.
    """
    asm = load_assembly(dll_path)
    summary = inspect_assembly(asm)

    # Read the TMSL content
    tmsl_text = Path(tmsl_path).read_text(encoding="utf-8")

    report = {"assembly": asm.FullName, "candidates": [], "invocations": []}

    for type_name, methods in summary.items():
        report["candidates"].append(type_name)
        # try to get the type and call likely methods
        try:
            t = asm.GetType(type_name)
            for mname in ("Deserialize", "Parse", "LoadFromXml", "ReadXml", "FromJson", "Load"):
                if mname in methods:
                    try:
                        method = t.GetMethod(mname)
                        # Determine parameter count
                        params = method.GetParameters()
                        if len(params) == 1:
                            # Accept a single string/stream — try passing the TMSL text
                            result = method.Invoke(None, Array[System.Object]([tmsl_text]))
                            report["invocations"].append({"type": type_name, "method": mname, "result": str(type(result))})
                        else:
                            report["invocations"].append({"type": type_name, "method": mname, "skipped": "unsupported-params"})
                    except Exception as ex:
                        report["invocations"].append({"type": type_name, "method": mname, "error": str(ex)})
        except Exception as ex:
            report.setdefault("errors", []).append({"type": type_name, "error": str(ex)})

    return report


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Load TOM assembly via pythonnet and inspect/try-parse TMSL/TMDL files")
    p.add_argument("dll", help="Path to Microsoft.AnalysisServices.Tabular.dll")
    p.add_argument("tmsl", nargs="?", help="Path to a TMSL/TMDL JSON or XML file to try parsing")
    args = p.parse_args()

    asm = load_assembly(args.dll)
    print("Loaded:", asm.FullName)
    summary = inspect_assembly(asm)
    print("Candidate types found:")
    for k in sorted(summary)[:40]:
        print("-", k)
    if args.tmsl:
        print("\nAttempting parse...")
        r = try_parse_tmsl(args.dll, args.tmsl)
        import json

        print(json.dumps(r, indent=2, default=str))
