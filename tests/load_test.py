#!/usr/bin/env python3
"""
Stress test: runs the addresses.txt test suite 1000 times (~53,000 requests)
using the jolly-mx-test.yaml configuration loaded in-process.

Console output from the log functions is suppressed via stdout redirection
during the test loop, then restored to print results.
"""

import os
import sys
import io
import re
import time
import importlib.util

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'jolly-mx-test.yaml')
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
ADDRESSES_PATH = os.path.join(SCRIPT_DIR, 'payloads/addresses.txt')

ITERATIONS = 5168

# Add project root to path so we can import src.config
sys.path.insert(0, PROJECT_DIR)

# Dynamically load jolly-mx.py because it contains a hyphen
spec = importlib.util.spec_from_file_location("jolly_mx", APP_PATH)
jmx = importlib.util.module_from_spec(spec)
sys.modules["jolly_mx"] = jmx
spec.loader.exec_module(jmx)

# Configure and load
jmx.config.config_file = CONFIG_PATH
jmx.config.verbose = True
jmx.config.load()

# Parse addresses.txt
lines = open(ADDRESSES_PATH, 'r').read().strip().split('\n')[1:]  # skip header
address_pairs = []
for line in lines:
    if not line.strip():
        continue
    parts = re.split(r'[\s]+', line.strip())
    if len(parts) >= 2:
        address_pairs.append((parts[0], parts[1]))

total_requests = len(address_pairs) * ITERATIONS
print(f"Stress test: {len(address_pairs)} addresses × {ITERATIONS} iterations = {total_requests:,} requests")

# Suppress stdout during the test loop
real_stdout = sys.stdout
sys.stdout = io.StringIO()

start_time = time.time()

for i in range(ITERATIONS):
    for sender, recipient in address_pairs:
        jmx.get_mx_for_message(sender, recipient, 3600)

elapsed = time.time() - start_time

# Restore stdout
sys.stdout = real_stdout

# Print results
rps = total_requests / elapsed if elapsed > 0 else 0
print(f"✅ Completed {total_requests:,} requests in {elapsed:.2f}s ({rps:,.0f} req/s)")
print()
print(jmx.config.print_usage())
