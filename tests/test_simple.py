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
PORT = 10101

def create_test_config():
    config_data = {
        'config': {
            'enabled': True,
            'log_file': '/var/log/jolly-mx.log',
            'bind_host': '127.0.0.1',
            'bind_port': PORT,
            'verbose': True
        },
        'servers': {
            'names': {
                'mx1': {'address': 'relay:[mx1.example.com]:25'},
                'mx2': {'address': 'relay:[mx2.example.com]:25'},
                'mx3': {'address': 'relay:[mx3.example.com]:25'}
            },
            'groups': {
                'good': ['mx1'],
                'bad': ['mx2'],
                'libero': ['mx3']
            }
        },
        'sender_rules': {
            'good.sender@example.com': 'good',
            'bad.sender@example.com': 'bad'
        },
        'recipient_rules': {
            'libero.it': 'libero'
        },
        'combined_rules': {
            'good,libero': ['mx1'],
            'bad,libero': ['mx2']
        }
    }
    
    fd, temp_path = tempfile.mkstemp(suffix='.yaml')
    with os.fdopen(fd, 'w') as f:
        yaml.dump(config_data, f)
        
    return temp_path

def start_server(test_config_path):
    print(f"Starting server on port {PORT}...")
    proc = subprocess.Popen(
        [sys.executable, APP_PATH, '-p', str(PORT), '-c', test_config_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    time.sleep(1) # wait for server to bind
    return proc

def send_request(sender, recipient):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(('127.0.0.1', PORT))
        request = f"request=smtpd_access_policy\nprotocol_state=RCPT\nprotocol_name=SMTP\nsender={sender}\nrecipient={recipient}\n\n"
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

def test_combined_rules():
    config_path = create_test_config()
    server_proc = start_server(config_path)
    
    try:
        print("\n--- Verifying Combined Rules ---")
        
        # Test Case 1: good sender + libero recipient -> mx1 (from combined rule)
        print("Test 1: good sender -> libero")
        resp1 = send_request('good.sender@example.com', 'user@libero.it')
        print(f"  Response: {resp1}")
        assert "FILTER relay:[mx1.example.com]:25" in resp1
        
        # Test Case 2: bad sender + libero recipient -> mx2 (from combined rule)
        print("Test 2: bad sender -> libero")
        resp2 = send_request('bad.sender@example.com', 'user@libero.it')
        print(f"  Response: {resp2}")
        assert "FILTER relay:[mx2.example.com]:25" in resp2

        # Test Case 3: unknown sender + libero recipient -> mx3 (from recipient rule fallback)
        print("Test 3: unknown sender -> libero")
        resp3 = send_request('unknown@example.com', 'user@libero.it')
        print(f"  Response: {resp3}")
        assert "FILTER relay:[mx3.example.com]:25" in resp3

        print("\n✅ Verification Passed!")
        
    except Exception as e:
        print(f"\n❌ Verification Failed: {e}")
        # print stderr if server crashed
        if server_proc.poll() is not None:
            stdout, stderr = server_proc.communicate()
            print(f"Server STDERR: {stderr.decode('utf-8')}")
        sys.exit(1)
    finally:
        server_proc.terminate()
        server_proc.wait()
        os.remove(config_path)

if __name__ == '__main__':
    test_combined_rules()
