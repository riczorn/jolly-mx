#!/usr/bin/env python3
import os
import sys
import yaml
import socket
import time
import subprocess
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
PORT = 10106

def start_server(config_path):
    print(f"Starting server on port {PORT} with config {config_path}...")
    proc = subprocess.Popen(
        [sys.executable, APP_PATH, '-p', str(PORT), '-c', config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1) # wait for server to bind
    return proc

def send_raw_request(sender, recipient):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(15.0)
        try:
            s.connect(('127.0.0.1', PORT))
            request_str = f"request=smtpd_access_policy\nprotocol_state=RCPT\nprotocol_name=SMTP\nsender={sender}\nrecipient={recipient}\n\n"
            s.sendall(request_str.encode('utf-8'))
            
            response = b""
            while True:
                data = s.recv(1024)
                if not data:
                    break
                response += data
                if b"\n\n" in response:
                    break
            
            # Parse the action out of the response
            resp_text = response.decode('utf-8').strip()
            if resp_text.startswith("action="):
                return resp_text.split("action=", 1)[1]
            return resp_text
        except Exception as e:
            return f"ERROR: {e}"

def main():
    parser = argparse.ArgumentParser(description="Debug routing rules using historical CSV data")
    parser.add_argument("-c", "--config", default=os.path.join(SCRIPT_DIR, "jolly-mx-test.yaml"),
                        help="Path to the jolly-mx.yaml configuration file")
    parser.add_argument("-i", "--input", default=os.path.join(PROJECT_DIR, "docs", "jolly-mx-messages.csv"),
                        help="Path to the input CSV file containing historical messages")
    
    args = parser.parse_args()
    
    config_path = os.path.abspath(args.config)
    input_csv = os.path.abspath(args.input)
    
    if not os.path.exists(config_path):
        print(f"ERROR: Configuration file not found: {config_path}")
        sys.exit(1)
        
    if not os.path.exists(input_csv):
        print(f"ERROR: Input CSV file not found: {input_csv}")
        sys.exit(1)

    # Parse config to ensure we don't read from the same file we write to
    try:
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f)
            
        csv_log_file = config_data.get('config', {}).get('csv_file', '/var/log/jolly-mx-messages.csv')
        
        if os.path.abspath(csv_log_file) == input_csv:
            print(f"ERROR: The input CSV file ({input_csv}) is the exact same file the server")
            print(f"is configured to log to! Please use a different file for debugging rules")
            print(f"to avoid infinite loop locking issues.")
            sys.exit(1)
            
    except Exception as e:
        print(f"WARNING: Could not parse config to check csv_file clash: {e}")

    print("\n--- Jolly-MX Rule Debugger ---")
    print(f"Config: {config_path}")
    print(f"Input:  {input_csv}")
    print("------------------------------\n")

    server_proc = start_server(config_path)
    if server_proc.poll() is not None:
        stdout, stderr = server_proc.communicate()
        print("Server failed to start!")
        print(f"STDERR: {stderr.decode('utf-8')}")
        sys.exit(1)
        
    total = 0
    errors = 0
    
    print(f"{'SENDER':<40} {'RECIPIENT':<40} {'ROUTING RESULT'}")
    print("-" * 105)

    try:
        with open(input_csv, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                    
                parts = line.split(';')
                if len(parts) < 3:
                     # Silently ignore malformed lines
                     continue
                
                # Format: date;sender;recipient;mx_group;mx_host
                sender = parts[1]
                recipient = parts[2]
                
                result = send_raw_request(sender, recipient)
                
                status_icon = "❌" if "ERROR" in result else "✅"
                if "ERROR" in result:
                    errors += 1
                    
                print(f"{sender[:38]:<40} {recipient[:38]:<40} {status_icon} {result}")
                total += 1
                
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        print(f"\n❌ Script encountered an error: {e}")
        errors += 1
    finally:
        server_proc.terminate()
        server_proc.wait()

    print("\n--- Debugging Session Complete ---")
    print(f"Processed: {total}")
    print(f"Errors:    {errors}")
    
    assert errors == 0, f"Encountered {errors} execution errors during the test run."
    print("✅ No execution failures occurred.")

if __name__ == '__main__':
    main()
