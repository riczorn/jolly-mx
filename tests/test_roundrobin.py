#!/usr/bin/env python3
import os
import sys
import yaml
import tempfile
import socket
import time
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
PORT = 10105

def create_test_config(roundrobin=True, enabled=True):
    default_action = 'ALL' if roundrobin else 'DUNNO'
    config_data = {
        'config': {
            'enabled': enabled,
            'log_file': '/var/log/jolly-mx.log',
            'bind_host': '127.0.0.1',
            'bind_port': PORT,
            'verbose': True
        },
        'servers': {
            'names': {
                'mx1': {'address': 'relay:[mx1.example.com]:25'},
                'mx2': {'address': 'relay:[mx2.example.com]:25'}
            },
            'groups': {
                'good': ['mx1', 'mx2']
            },
            'default': default_action
        },
        'sender_rules': {
            'vip_sender@example.com': 'good'
        }
    }
    
    fd, temp_path = tempfile.mkstemp(suffix='.yaml')
    with os.fdopen(fd, 'w') as f:
        yaml.dump(config_data, f)
        
    return temp_path

def start_server(test_config_path):
    print(f"Starting server on port {PORT} with config {test_config_path}...")
    proc = subprocess.Popen(
        [sys.executable, APP_PATH, '-p', str(PORT), '-c', test_config_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(1) # wait for server to bind
    return proc

def send_raw_request(request_str):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        try:
            s.connect(('127.0.0.1', PORT))
            s.sendall(request_str.encode('utf-8'))
            
            response = b""
            while True:
                data = s.recv(1024)
                if not data:
                    break
                response += data
                if b"\n\n" in response:
                    break
            return response.decode('utf-8').strip()
        except Exception as e:
            return f"ERROR: {e}"

def craft_request(sender):
    return f"request=smtpd_access_policy\nsasl_username={sender}\nprotocol_state=RCPT\nprotocol_name=SMTP\nsender={sender}\nrecipient=bob@example.com\n\n"

def test_roundrobin_behaviors():
    print("\n--- Testing Roundrobin and Enabled Flags ---")
    failed = 0
    passed = 0

    # 1. Test enabled=True, roundrobin=True
    config_path = create_test_config(roundrobin=True, enabled=True)
    server_proc = start_server(config_path)
    
    try:
        print("\nTest 1: enabled=True, roundrobin=True (Fallback to Global Pool)")
        req1 = craft_request("random1@unknown.com")
        resp1 = send_raw_request(req1)
        if "action=FILTER relay:[mx1.example.com]:25" in resp1 or "action=FILTER relay:[mx2.example.com]:25" in resp1:
            print("  ✅ Random sender fell back to global pool MX server")
            passed += 1
        else:
            print(f"  ❌ Expected global MX server, got: {resp1!r}")
            failed += 1
            
        # Send a second random sender and expect it to work too
        req2 = craft_request("random2@unknown.com")
        resp2 = send_raw_request(req2)
        if "action=FILTER relay:[mx1.example.com]:25" in resp2 or "action=FILTER relay:[mx2.example.com]:25" in resp2:
            print("  ✅ Second random sender also fell back to global pool MX server successfully")
            passed += 1
        else:
            print(f"  ❌ Expected global MX server, got: {resp2!r}")
            failed += 1
    finally:
        server_proc.terminate()
        server_proc.wait()
        os.remove(config_path)

    # 2. Test enabled=True, roundrobin=False
    config_path = create_test_config(roundrobin=False, enabled=True)
    server_proc = start_server(config_path)
    
    try:
        print("\nTest 2: enabled=True, roundrobin=False (Fallback generates DUNNO)")
        req1 = craft_request("random3@unknown.com")
        resp1 = send_raw_request(req1)
        if resp1 == "action=DUNNO":
            print("  ✅ Random sender generated DUNNO (Fallback pool disabled)")
            passed += 1
        else:
            print(f"  ❌ Expected DUNNO, got: {resp1!r}")
            failed += 1
    finally:
        server_proc.terminate()
        server_proc.wait()
        os.remove(config_path)

    # 3. Test enabled=False (Everything is DUNNO)
    config_path = create_test_config(roundrobin=True, enabled=False)
    server_proc = start_server(config_path)
    
    try:
        print("\nTest 3: enabled=False (All matching rules generate DUNNO)")
        # This one matches the valid rule!
        req1 = craft_request("vip_sender@example.com")
        resp1 = send_raw_request(req1)
        if resp1 == "action=DUNNO":
            print("  ✅ VIP sender matched but still generated DUNNO (plugin disabled globally)")
            passed += 1
        else:
            print(f"  ❌ Expected DUNNO, got: {resp1!r}")
            failed += 1
    finally:
        server_proc.terminate()
        server_proc.wait()
        os.remove(config_path)

    print(f"\nResults: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)

if __name__ == '__main__':
    test_roundrobin_behaviors()
