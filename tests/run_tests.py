import sys
import importlib.util
import re
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location("jolly_mx", "../jolly-mx.py")
jmx = importlib.util.module_from_spec(spec)
sys.modules["jolly_mx"] = jmx
spec.loader.exec_module(jmx)

# Mock args to prevent errors during loading
jmx.args = type('Args', (), {'config': '../jolly-mx.yaml', 'debug': False})()
jmx.config.load('../jolly-mx.yaml')

lines = open('addresses.txt', 'r').read().strip().split('\n')[1:] # skip header

print("| Sender | Recipient | MX Group | MX Host |")
print("|---|---|---|---|")

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
        
    print(f"| {sender} | {recipient} | {group_matched} | {mx_host} |")
