import shutil
import subprocess
from typing import List, Optional


def find_sml_converters() -> Optional[str]:
    """Return the executable to call for sml-converters, preferring a global install.

    Falls back to 'npx sml-converters' if available in the environment.
    """
    exe = shutil.which("sml-converters")
    if exe:
        return exe
    # rely on npx being available
    if shutil.which("npx"):
        return "npx sml-converters"
    return None


def run_sml_converters(args: List[str]) -> subprocess.CompletedProcess:
    """Run sml-converters with the given args. Returns CompletedProcess.

    Example: run_sml_converters(["--version"]) or run_sml_converters(["dbt-to-sml","-s","./src","-o","./out"])
    """
    exe = find_sml_converters()
    if not exe:
        raise FileNotFoundError("sml-converters not found on PATH and npx not available")

    # if exe contains a space (npx sml-converters), invoke via shell
    if " " in exe:
        cmd = "".join([exe, " "]) + " ".join(args)
        return subprocess.run(cmd, capture_output=True, text=True, shell=True)
    else:
        cmd = [exe] + args
        return subprocess.run(cmd, capture_output=True, text=True)


if __name__ == "__main__":
    import sys
    if len(sys.argv) <= 1:
        print("Usage: run_sml_converter.py [args for sml-converters]")
        sys.exit(1)
    proc = run_sml_converters(sys.argv[1:])
    print(proc.stdout)
    sys.exit(proc.returncode)
