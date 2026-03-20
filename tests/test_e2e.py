#!/usr/bin/env python3

import os
import sys
import time
import socket
import subprocess
import tempfile
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
CONFIG_PATH = os.path.join(PROJECT_DIR, 'tests/jolly-mx-test.yaml')
ADDRESSES_PATH = os.path.join(SCRIPT_DIR, 'addresses.txt')
PORT = 10100

def create_test_config():
    with open(CONFIG_PATH, 'r') as f:
        config_data = yaml.safe_load(f)
        
    # Enable the service for E2E tests
    if 'config' not in config_data:
        config_data['config'] = {}
    if not config_data['config']:
        config_data['config'] = {}
        
    config_data['config']['enabled'] = True
    config_data['config']['log_file'] = False # Don't litter logs during e2e testing
    
    fd, temp_path = tempfile.mkstemp(suffix='.yaml')
    with os.fdopen(fd, 'w') as f:
        yaml.dump(config_data, f)
        
    return temp_path

def start_server(test_config_path):
    print(f"Starting server on port {PORT}...")
    proc = subprocess.Popen(
        [sys.executable, APP_PATH, '-p', str(PORT), '-c', test_config_path, '-q'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(1) # wait for server to bind
    return proc

def send_request(sender, recipient):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(('127.0.0.1', PORT))
        request = f"request=smtpd_access_policy\n" \
                  f"protocol_state=RCPT\n" \
                  f"protocol_name=SMTP\n" \
                  f"sender={sender}\n" \
                  f"recipient={recipient}\n\n"
        s.sendall(request.encode('utf-8'))
        
        response = b""
        while True:
            data = s.recv(1024)
            if not data:
                break
            response += data
            if b"\n\n" in response:
                break
                
        return response.decode('utf-8').strip()

def main():
    test_config_path = create_test_config()
    server_proc = start_server(test_config_path)
    
    if server_proc.poll() is not None:
        stdout, stderr = server_proc.communicate()
        print("Server failed to start!")
        print(f"STDOUT: {stdout.decode('utf-8')}")
        print(f"STDERR: {stderr.decode('utf-8')}")
        os.remove(test_config_path)
        sys.exit(1)
        
    try:
        with open(ADDRESSES_PATH, 'r') as f:
            lines = f.readlines()
            
        print("\n--- Running End-to-End Tests ---")
        # skip header
        for line in lines:
            line = line.strip()
            if not line or line.startswith('sender'):
                continue
                
            parts = line.split('\t')
            if len(parts) >= 2:
                sender = parts[0].strip()
                recipient = parts[1].strip()
                print(f"Request: sender={sender} recipient={recipient}")
                try:
                    response = send_request(sender, recipient)
                    print(f"  --> Response: {response}")
                except Exception as e:
                    print(f"  --> Error: {e}")
            else:
                print(f"Skipping unparseable line: {line}")
                
        print("--- Tests Complete ---\n")
                
    finally:
        print("Stopping server...")
        server_proc.terminate()
        server_proc.wait()
        os.remove(test_config_path)

if __name__ == '__main__':
    main()
