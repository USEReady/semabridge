import sys
import os

sys.path.insert(0, r"C:\Users\MANOJ\dev-test\semabridge\src")

try:
    from semabridge.api.app_setup import configure_app
    print("Imports worked")
except Exception as e:
    print(e)
