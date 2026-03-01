import sys
import importlib.util
import re
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.yaml')
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
ADDRESSES_PATH = os.path.join(SCRIPT_DIR, 'addresses.txt')

# Add project root to path so we can import src.config
sys.path.insert(0, PROJECT_DIR)

# Dynamically load jolly-mx.py because it contains a hyphen
spec = importlib.util.spec_from_file_location("jolly_mx", APP_PATH)
jolly_mx = importlib.util.module_from_spec(spec)
sys.modules["jolly_mx"] = jolly_mx
spec.loader.exec_module(jolly_mx)

jmx = jolly_mx

class MockArgs:
    verbose = True
    quiet = False

jmx.args = MockArgs()

jmx.config.load(CONFIG_PATH)

print("-------------")
jmx.config.test() # perform a few lookups to test the round robin
print("-------------")
jmx.print_stats()
print("-------------")
jmx.config.print_usage()