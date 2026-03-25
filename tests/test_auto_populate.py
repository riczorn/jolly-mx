#!/usr/bin/env python3
import os
import sys
import tempfile
import socket
import time
import subprocess
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'jolly-mx-test.yaml')
PAYLOADS_DIR = os.path.join(SCRIPT_DIR, 'payloads')
VIRTUAL_FILE = os.path.join(PAYLOADS_DIR, 'virtual')
PORT = 10104

def test_auto_populate():
    with open(CONFIG_PATH, 'r') as f:
        config_data = yaml.safe_load(f)

    if 'config' not in config_data:
        config_data['config'] = {}
    
    fd, csv_path = tempfile.mkstemp(suffix='.csv')
    os.close(fd)

    config_data['config']['enabled'] = True
    config_data['config']['bind_host'] = '127.0.0.1'
    config_data['config']['bind_port'] = PORT
    config_data['config']['auto_populate_local_domains'] = True
    config_data['config']['postfix_virtual_file'] = VIRTUAL_FILE
    config_data['config']['csv_file'] = csv_path
    
    fd2, temp_config_path = tempfile.mkstemp(suffix='.yaml')
    with os.fdopen(fd2, 'w') as f:
        yaml.dump(config_data, f)
        
    proc = subprocess.Popen(
        [sys.executable, APP_PATH, '-p', str(PORT), '-c', temp_config_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(1)

    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        print("Server failed to start!")
        print(f"STDOUT: {stdout.decode('utf-8')}")
        print(f"STDERR: {stderr.decode('utf-8')}")
        os.remove(temp_config_path)
        os.remove(csv_path)
        sys.exit(1)

    # In jolly-mx, client_address from a non-local network triggers mail direction logic
    # We will use an external IP not matching local_networks explicitly, e.g. 192.168.1.100
    payload1 = "request=smtpd_access_policy\nprotocol_state=RCPT\nprotocol_name=SMTP\nsender=sender@example.com\nrecipient=user@domain1.net\nclient_address=192.168.1.100\n\n"
    payload2 = "request=smtpd_access_policy\nprotocol_state=RCPT\nprotocol_name=SMTP\nsender=sender@example.com\nrecipient=user@domain3.com\nclient_address=192.168.1.100\n\n"
    payload3 = "request=smtpd_access_policy\nprotocol_state=RCPT\nprotocol_name=SMTP\nsender=sender@example.com\nrecipient=user@domain2.org\nclient_address=192.168.1.100\n\n"

    try:
        for payload in [payload1, payload2, payload3]:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(('127.0.0.1', PORT))
                s.sendall(payload.encode('utf-8'))
                response = b""
                while True:
                    data = s.recv(1024)
                    if not data:
                        break
                    response += data
                    if b"\n\n" in response:
                        break
    except Exception as e:
        print(f"Error sending payload: {e}")

    proc.terminate()
    proc.wait(timeout=5)

    with open(csv_path, 'r') as f:
        lines = f.readlines()

    passed = 0
    failed = 0
    expected_results = [
        ("domain1.net", "INCOMING"),
        ("domain3.com", "REJECTED"),
        ("domain2.org", "INCOMING"),
    ]

    for idx, (name, expected) in enumerate(expected_results):
        if idx < len(lines):
            csv_line = lines[idx].strip()
            direction = csv_line.split(';')[-1] if len(csv_line.split(';')) >= 7 else "UNKNOWN"
            if direction == expected:
                print(f"  ✅ {name} -> {direction}")
                passed += 1
            else:
                print(f"  ❌ {name} -> expected {expected}, got {direction}. CSV Line: {csv_line}")
                failed += 1
        else:
            print(f"  ❌ {name} -> Missing CSV line")
            failed += 1

    os.remove(temp_config_path)
    os.remove(csv_path)

    print(f"\n--- Final Results: {passed} passed, {failed} failed ---")
    if failed > 0:
        sys.exit(1)
    else:
        print("\n✅ Auto-populate test passed!")

if __name__ == '__main__':
    test_auto_populate()
