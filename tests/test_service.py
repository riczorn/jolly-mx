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
HOST = '127.0.0.1'

def get_config():
    with open(CONFIG_PATH, 'r') as f:
        config_data = yaml.safe_load(f)
        
    # Enable the service for E2E tests
    if 'config' not in config_data:
        config_data['config'] = {}
    if not config_data['config']:
        config_data['config'] = {}
        
    config_data['config']['enabled'] = True
    config_data['config']['log_file'] = False # Don't litter logs during e2e testing
    global PORT
    global HOST
    PORT = config_data['config']['bind_port']
    HOST = config_data['config']['bind_host']
    
    return config_data

def send_request(sender, recipient):
    global PORT
    global HOST
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
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
    config = get_config()
    
        
    try:
        with open(ADDRESSES_PATH, 'r') as f:
            lines = f.readlines()
        global PORT
        global HOST
            
        print(f"\n--- Running End-to-End Service Tests on {HOST}:{PORT} ---")
        
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
        print("Finished.")
        

if __name__ == '__main__':
    main()
