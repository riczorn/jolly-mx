#!/usr/bin/env python3
import os
import sys
import yaml
import tempfile
import socket
import time
import subprocess
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'jolly-mx-test.yaml')
PAYLOADS_DIR = os.path.join(SCRIPT_DIR, 'payloads')
PORT = 10103


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        config_data = yaml.safe_load(f)

    if 'config' not in config_data:
        config_data['config'] = {}
    
    config_data['config']['enabled'] = True
    config_data['config']['bind_host'] = '127.0.0.1'
    config_data['config']['bind_port'] = PORT
    return config_data


def run_test_phase(local_domains):
    config_data = load_config()
    
    # Create a temporary CSV file
    fd, csv_path = tempfile.mkstemp(suffix='.csv')
    os.close(fd)
    config_data['config']['csv_file'] = csv_path
    config_data['config']['local_domains'] = local_domains
    
    # Create temp config YAML
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

    files_tested = []
    
    try:
        payload_files = sorted(glob.glob(os.path.join(PAYLOADS_DIR, '*.txt')))
        for payload_path in payload_files:
            filename = os.path.basename(payload_path)
            
            with open(payload_path, 'r') as f:
                payload = f.read()

            recipient_domain = ""
            for line in payload.split('\n'):
                if line.strip().startswith('recipient='):
                    recip = line.split('=', 1)[1].strip()
                    if '@' in recip:
                        recipient_domain = recip.split('@')[-1].lower()
                    else:
                        recipient_domain = recip.lower()
                    break

            if filename.startswith('in_'):
                if not local_domains:
                    expected = 'INCOMING'
                else:
                    is_local = False
                    for ld in local_domains:
                        ld = ld.lower()
                        if recipient_domain == ld or recipient_domain.endswith('.' + ld):
                            is_local = True
                            break
                    expected = 'INCOMING' if is_local else 'REJECTED'
            elif filename.startswith('out_'):
                expected = 'OUTGOING'
            else:
                continue
                
            # clean up payload to normalize newlines
            payload = payload.replace('\r\n', '\n')
            # ensure it ends with \n\n
            if not payload.endswith('\n\n'):
                if payload.endswith('\n'):
                    payload += '\n'
                else:
                    payload += '\n\n'
                
            try:
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
                files_tested.append((filename, expected))
            except Exception as e:
                print(f"Error sending payload {filename}: {e}")

    finally:
        proc.terminate()
        proc.wait(timeout=5)
        
    passed = 0
    failed = 0
    
    with open(csv_path, 'r') as f:
        lines = f.readlines()
        
    if len(lines) != len(files_tested):
        print(f"Warning: Expected {len(files_tested)} CSV lines but found {len(lines)}")
        
    for idx, (filename, expected) in enumerate(files_tested):
        if idx < len(lines):
            csv_line = lines[idx].strip()
            parts = csv_line.split(';')
            direction = parts[6] if len(parts) >= 7 else "UNKNOWN"
            
            if direction == expected:
                print(f"  ✅ {filename} -> {direction}")
                passed += 1
            else:
                print(f"  ❌ {filename} -> expected {expected}, got {direction}. CSV Line: {csv_line}")
                failed += 1
        else:
            print(f"  ❌ {filename} -> Missing CSV line")
            failed += 1

    os.remove(temp_config_path)
    os.remove(csv_path)
    return passed, failed

def test_directions():
    total_passed = 0
    total_failed = 0
    
    phases = [
        [],
        ['example.com'],
        ['example.net']
    ]
    
    for domains in phases:
        print(f"\n--- Testing with local_domains: {domains} ---")
        passed, failed = run_test_phase(domains)
        total_passed += passed
        total_failed += failed
        print(f"Phase Results: {passed} passed, {failed} failed")

    print(f"\n--- Final Results: {total_passed} passed, {total_failed} failed ---")
    
    if total_failed > 0:
        sys.exit(1)
    else:
        print("\n✅ All direction tests passed!")

if __name__ == '__main__':
    test_directions()
