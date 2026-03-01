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

spec = importlib.util.spec_from_file_location("jolly_mx", APP_PATH)
jmx = importlib.util.module_from_spec(spec)
sys.modules["jolly_mx"] = jmx
spec.loader.exec_module(jmx)

# Mock args to prevent errors during loading
jmx.args = type('Args', (), {'config': CONFIG_PATH, 'debug': False, 'verbose': False, 'quiet': True})()
jmx.config.load(CONFIG_PATH)

lines = open(ADDRESSES_PATH, 'r').read().strip().split('\n')[1:] # skip header

print(f"Sender\tRecipient\tMX Group\tMX Host")

for line in lines:
    if not line.strip(): continue
    parts = re.split(r'[\s]+', line.strip())
    if len(parts) >= 2:
        sender = parts[0]
        recipient = parts[1]
    else:
        continue

    mx, group = jmx.get_mx_for_message(sender, recipient, 3600)
        
    print(f"{sender}\t{recipient}\t{group}\t{mx}")



