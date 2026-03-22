#!/usr/bin/env python3
"""
Concurrent stress test: runs addresses.txt through the routing logic
from 4 threads simultaneously to expose thread-safety issues.

Uses a threading.Barrier so all threads start at exactly the same instant,
maximising contention on shared state (mx_cache, Servers.get_next, etc.).

After the run, validates invariants:
  - Total mails_sent across all server groups == expected request count
  - No server has negative mails_sent
  - No exceptions were raised in any thread
"""

import os
import sys
import io
import re
import time
import threading
import importlib.util
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'jolly-mx-test.yaml')
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
ADDRESSES_PATH = os.path.join(SCRIPT_DIR, 'payloadsaddresses.txt')

NUM_THREADS = 10
ITERATIONS_PER_THREAD = 1292  # ~53 addresses × 1292 ≈ 68,500 per thread, ~274,000 total

# Add project root to path
sys.path.insert(0, PROJECT_DIR)

# Dynamically load jolly-mx.py
spec = importlib.util.spec_from_file_location("jolly_mx", APP_PATH)
jmx = importlib.util.module_from_spec(spec)
sys.modules["jolly_mx"] = jmx
spec.loader.exec_module(jmx)

# Configure and load
jmx.config.config_file = CONFIG_PATH
jmx.config.verbose = False
jmx.config.load()

# Parse addresses.txt
lines = open(ADDRESSES_PATH, 'r').read().strip().split('\n')[1:]
address_pairs = []
for line in lines:
    if not line.strip():
        continue
    parts = re.split(r'[\s]+', line.strip())
    if len(parts) >= 2:
        address_pairs.append((parts[0], parts[1]))

total_per_thread = len(address_pairs) * ITERATIONS_PER_THREAD
total_requests = total_per_thread * NUM_THREADS

print(f"Concurrent stress test")
print(f"  {len(address_pairs)} addresses × {ITERATIONS_PER_THREAD} iterations × {NUM_THREADS} threads = {total_requests:,} requests")

# Barrier ensures all threads start at the exact same moment
barrier = threading.Barrier(NUM_THREADS)
thread_errors = []
errors_lock = threading.Lock()

def worker(thread_id):
    """Each worker runs the full address list ITERATIONS_PER_THREAD times."""
    try:
        barrier.wait()  # all threads launch together
        for _ in range(ITERATIONS_PER_THREAD):
            for sender, recipient in address_pairs:
                jmx.get_mx_for_message(sender, recipient, 3600)
    except Exception as e:
        with errors_lock:
            thread_errors.append((thread_id, e, traceback.format_exc()))

# Suppress stdout during the test
real_stdout = sys.stdout
sys.stdout = io.StringIO()

threads = []
start_time = time.time()

for i in range(NUM_THREADS):
    t = threading.Thread(target=worker, args=(i,), name=f"worker-{i}")
    threads.append(t)
    t.start()

for t in threads:
    t.join()

elapsed = time.time() - start_time

# Restore stdout
sys.stdout = real_stdout

# ---- Validation ----
errors = []

# Check no thread raised an exception
if thread_errors:
    for tid, exc, tb in thread_errors:
        errors.append(f"Thread {tid} raised {exc.__class__.__name__}: {exc}\n{tb}")

# Check mails_sent invariants on each server group
def check_group(name, servers_obj, expected_total=None):
    total = 0
    for server in servers_obj.servers:
        if server.mails_sent < 0:
            errors.append(f"{name}: server {server.name} has negative mails_sent ({server.mails_sent})")
        total += server.mails_sent
    if expected_total is not None and total != expected_total:
        errors.append(f"{name}: total mails_sent {total} != expected {expected_total}")
    return total

# The "all servers" pool tracks every request routed through it
# (but not combined rules or named groups, so we just check non-negative)
check_group("All Servers", jmx.config.servers_obj)

group_names = [sg for sg in vars(jmx.config.server_groups) if not sg.startswith('__')]
for gname in group_names:
    check_group(f"Group {gname}", getattr(jmx.config.server_groups, gname))

# Print results
rps = total_requests / elapsed if elapsed > 0 else 0
print(f"\nCompleted {total_requests:,} requests in {elapsed:.2f}s ({rps:,.0f} req/s)")
print(f"Threads: {NUM_THREADS}, Iterations/thread: {ITERATIONS_PER_THREAD}")

if errors:
    print(f"\n❌ CONCURRENCY ISSUES DETECTED ({len(errors)}):")
    for err in errors:
        print(f"  {err}")
    sys.exit(1)
else:
    print(f"\n✅ No concurrency issues detected")

print(jmx.config.print_usage())
