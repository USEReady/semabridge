#!/usr/bin/env python
"""
Convenience wrapper for running Semabridge CLI.

Usage:
    python main.py [COMMAND] [OPTIONS]

This is equivalent to:
    python -m semabridge [COMMAND] [OPTIONS]
"""

import sys
import os
from dotenv import load_dotenv

# Load .env before anything else so SEMABRIDGE_DATABASE_URL and all other
# env-vars are available to the entire process from startup.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from semabridge.cli.main import main

if __name__ == "__main__":
    main()
