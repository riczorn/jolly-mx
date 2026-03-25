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
PORT = 10103

def create_test_config(allowed_clients=None):
    if allowed_clients is None:
        allowed_clients = []
        
    config_data = {
        'config': {
            'enabled': True,
            'log_file': '/var/log/jolly-mx.log',
            'bind_host': '127.0.0.1',
            'bind_port': PORT,
            'verbose': True,
            'allowed_clients': allowed_clients
        },
        'servers': {
            'hosts': {
                'mx1': {'address': 'relay:[mx1.example.com]:25'}
            },
            'groups': {
                'good': ['mx1']
            }
        },
        'sender_rules': {
            'default': 'good'
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

def test_improper_usage():
    print("\n--- Testing Improper Usage ---")
    failed = 0
    passed = 0

    # 1. Test IP Blocked (allowed_clients: 8.8.8.8)
    config_path_blocked = create_test_config(allowed_clients=['8.8.8.8'])
    server_proc = start_server(config_path_blocked)
    
    try:
        print("\nTest 1: IP Blocked (allowed_clients = 8.8.8.8)")
        valid_request = "request=smtpd_access_policy\nprotocol_state=RCPT\nprotocol_name=SMTP\nsender=alice@example.com\nrecipient=bob@example.com\n\n"
        resp = send_raw_request(valid_request)
        if resp == "" or "Connection reset" in resp:
            print(f"  ✅ Connection cleanly closed/reset by server as expected (got {resp!r})")
            passed += 1
        else:
            print(f"  ❌ Expected empty response or connection reset, got: {resp!r}")
            failed += 1
    finally:
        server_proc.terminate()
        server_proc.wait()
        os.remove(config_path_blocked)

    # 2. Test input sanitization (allowed_clients: 0.0.0.0)
    config_path_allowed = create_test_config(allowed_clients=["0.0.0.0"])
    server_proc = start_server(config_path_allowed)
    
    try:
        print("\nTest 2: MAX_REQUEST_SIZE exceeded")
        huge_request = "request=smtpd_access_policy\nprotocol_state=RCPT\nprotocol_name=SMTP\n"
        huge_request += "x=" + ("A" * 11000) + "\n"
        huge_request += "sender=alice@example.com\nrecipient=bob@example.com\n\n"
        
        resp = send_raw_request(huge_request)
        if resp == "action=DUNNO":
            print("  ✅ Server responded with DUNNO as expected")
            passed += 1
        else:
            print(f"  ❌ Expected action=DUNNO, got: {resp!r}")
            failed += 1
            
        print("\nTest 3: Missing protocol_name field")
        missing_proto = "request=smtpd_access_policy\nprotocol_state=RCPT\nsender=alice@example.com\nrecipient=bob@example.com\n\n"
        resp = send_raw_request(missing_proto)
        if resp == "action=DUNNO":
            print("  ✅ Server responded with DUNNO as expected")
            passed += 1
        else:
            print(f"  ❌ Expected action=DUNNO, got: {resp!r}")
            failed += 1

        print("\nTest 4: Invalid sender email format")
        bad_email = "request=smtpd_access_policy\nprotocol_state=RCPT\nprotocol_name=SMTP\nsender=alice@@example...com\nrecipient=bob@example.com\n\n"
        resp = send_raw_request(bad_email)
        if resp == "action=DUNNO":
            print("  ✅ Server responded with DUNNO as expected")
            passed += 1
        else:
            print(f"  ❌ Expected action=DUNNO, got: {resp!r}")
            failed += 1

    finally:
        server_proc.terminate()
        server_proc.wait()
        os.remove(config_path_allowed)

    print(f"\nResults: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)

if __name__ == '__main__':
    test_improper_usage()
