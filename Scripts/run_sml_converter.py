from semabridge.sml.converter_wrapper import run_sml_converters
import sys


def main():
    if len(sys.argv) <= 1:
        print("Usage: run_sml_converter.py [sml-converters args]")
        return 2
    proc = run_sml_converters(sys.argv[1:])
    print(proc.stdout)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
