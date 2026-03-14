#!/usr/bin/env python3
"""
Diagnostic script to test if logging is working properly on the backend.

This script replicates the exact logging setup from main.py and verifies
that log records are actually reaching console handlers.
"""

import sys
import os
import logging

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from semabridge.utils.logger import setup_logging, get_logger

print("=" * 70)
print("LOGGING DIAGNOSTIC TEST")
print("=" * 70)

# Test 1: Direct print (baseline)
print("\n[TEST 1] Direct print to stdout:")
print("  ✓ This message is visible directly")

# Test 2: Setup logging with INFO level
print("\n[TEST 2] Setting up logging with INFO level...")
setup_logging(level="INFO")
print("  ✓ setup_logging(level='INFO') completed")

# Test 3: Get root logger and check handlers
print("\n[TEST 3] Checking root logger handlers:")
root_logger = logging.getLogger()
print(f"  Root logger level: {logging.getLevelName(root_logger.level)}")
print(f"  Root logger handlers: {len(root_logger.handlers)}")
for i, handler in enumerate(root_logger.handlers):
    print(f"    Handler {i}: {handler.__class__.__name__}")
    print(f"      Level: {logging.getLevelName(handler.level)}")
    print(f"      Formatter: {handler.formatter}")

# Test 4: Test logger.info() at different levels
print("\n[TEST 4] Testing logger.info() output:")
test_logger = get_logger("test_module")
print(f"  Logger name: {test_logger.name}")
print(f"  Logger level: {logging.getLevelName(test_logger.level)}")
print(f"  Logger propagate: {test_logger.propagate}")

print("  About to call logger.info()...")
test_logger.info("✓ This is an INFO level test message")
test_logger.warning("✓ This is a WARNING level test message")
test_logger.error("✓ This is an ERROR level test message")

# Test 5: Simulate ExecutionEngine logger
print("\n[TEST 5] Testing with 'semabridge.core' logger (like ExecutionEngine):")
engine_logger = get_logger("semabridge.core.execution_engine")
engine_logger.info("✓ ExecutionEngine: Step 1 - Configuration initialized")
engine_logger.info("✓ ExecutionEngine: Step 2 - Source validated")
engine_logger.info("✓ ExecutionEngine: Step 9 - Deploying...")

# Test 6: Install WebSocket handler and test again
print("\n[TEST 6] Installing WebSocket alert handler...")
from semabridge.api.websocket_alerts import install_websocket_alert_handler
install_websocket_alert_handler()
print("  ✓ install_websocket_alert_handler() completed")

print("  Checking root logger handlers again:")
root_logger = logging.getLogger()
print(f"  Root logger handlers: {len(root_logger.handlers)}")
for i, handler in enumerate(root_logger.handlers):
    print(f"    Handler {i}: {handler.__class__.__name__}")
    print(f"      Level: {logging.getLevelName(handler.level)}")

# Test 7: Test logging after WebSocket handler installed
print("\n[TEST 7] Testing logger output after WebSocket handler installed:")
engine_logger.info("✓ Still logging after WebSocket handler installed")
engine_logger.warning("⚠ This warning should appear")

print("\n" + "=" * 70)
print("DIAGNOSTIC TEST COMPLETE")
print("=" * 70)
print("""
INTERPRETATION:
- If you see all ✓ messages above: Logging is working correctly
- If you see NO ✓ messages in TEST 4+: The RichHandler is not outputting to console
- If you see NO ✓ messages in TEST 7: The WebSocket handler is interfering
- If logs are missing: Check if output is being buffered (run with: python -u)
""")
