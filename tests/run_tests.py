import sys
import importlib.util
import re
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.yaml')
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
ADDRESSES_PATH = os.path.join(SCRIPT_DIR, 'addresses.txt')

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
    parts = re.split(r'[\s:]+', line.strip())
    if len(parts) >= 2:
        sender = parts[0]
        recipient = parts[1]
    else:
        continue
        
    action = "DUNNO"
    group_matched = "n/a"
    mx_host = "n/a"
    
    mx, group = jmx.get_next_server_for_email(recipient, 3600, rule_type="recipient_rules")
    if mx and mx != "NO RESULT":
        group_matched = group
        mx_host = mx
        action = "FILTER"
        
    if action == "DUNNO" and sender:
        mx, group = jmx.get_next_server_for_email(sender, 3600, rule_type="sender_rules")
        if mx and mx != "NO RESULT":
            group_matched = group
            mx_host = mx
            action = "FILTER"
            
    if action == "DUNNO":
        mx_host = "DUNNO (Postfix Default)"
        group_matched = "DUNNO"
        
    print(f"{sender}\t{recipient}\t{group_matched}\t{mx_host}")
