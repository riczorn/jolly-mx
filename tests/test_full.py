#!/usr/bin/env python3
"""
Test combined rules using the jolly-mx-test.yaml configuration.

Reads each line from addresses.txt and verifies that the server response
matches the expected result in the third column.  The expected result is
either a group name (e.g. "good", "bad", "gmail") — in which case the
returned relay must belong to one of the servers in that group — or an
explicit server list like "[mx5, mx6]".
"""

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
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'jolly-mx-test.yaml')
ADDRESSES_PATH = os.path.join(SCRIPT_DIR, 'addresses.txt')
PORT = 10102


def load_config():
    """Load and patch the YAML config for testing."""
    with open(CONFIG_PATH, 'r') as f:
        config_data = yaml.safe_load(f)

    # Enable the service and suppress log files
    if 'config' not in config_data:
        config_data['config'] = {}
    if not config_data['config']:
        config_data['config'] = {}

    config_data['config']['enabled'] = True
    # config_data['config']['log_file'] = False
    config_data['config']['verbose'] = True
    config_data['config']['bind_host'] = '127.0.0.1'
    config_data['config']['bind_port'] = PORT

    return config_data


def build_group_addresses(config_data):
    """
    Build a mapping from group name -> set of valid relay addresses.
    Also returns a mapping from server name -> relay address.
    """
    servers = config_data.get('servers', {})
    names = servers.get('names', {})

    # server name -> address string, e.g. "mx1" -> "relay:[mx1.example.com]:25"
    server_addresses = {}
    for name, info in names.items():
        server_addresses[name] = info['address']

    # group name -> set of addresses
    group_addresses = {}
    groups = servers.get('groups', {})
    for key, value in groups.items():
        if isinstance(value, list):
            group_addresses[key] = {server_addresses[s] for s in value}

    return server_addresses, group_addresses


def expected_addresses(expected, server_addresses, group_addresses):
    """
    Given the expected result string from addresses.txt, return the set of
    valid relay addresses, or the special string 'DUNNO'.

    expected can be:
      - "DUNNO" (no routing decision)
      - a group name like "good" or "gmail"
      - an explicit list like "[mx5, mx6]"
    """
    expected = expected.strip()

    if expected == 'DUNNO':
        return 'DUNNO'

    # Explicit server list: "[mx5, mx6]" or "[mx7]"
    if expected.startswith('[') and expected.endswith(']'):
        inner = expected[1:-1]
        names = [n.strip() for n in inner.split(',')]
        return {server_addresses[n] for n in names}

    # Group name
    if expected in group_addresses:
        return group_addresses[expected]

    raise ValueError(f"Unknown expected result: {expected!r}")


def create_test_config(config_data):
    """Write the patched config to a temporary file."""
    fd, temp_path = tempfile.mkstemp(suffix='.yaml')
    with os.fdopen(fd, 'w') as f:
        yaml.dump(config_data, f)
    return temp_path


def start_server(test_config_path):
    print(f"Starting server on port {PORT}...")
    proc = subprocess.Popen(
        [sys.executable, APP_PATH, '-p', str(PORT), '-c', test_config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(1)  # wait for server to bind
    return proc


def send_request(sender, recipient):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(('127.0.0.1', PORT))
        request = (
            f"request=smtpd_access_policy\n"
            f"protocol_state=RCPT\n"
            f"protocol_name=SMTP\n"
            f"sender={sender}\n"
            f"recipient={recipient}\n\n"
        )
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


def parse_response_address(response):
    """
    Extract the relay address from a response like
    'action=FILTER relay:[mx1.example.com]:25'
    Returns the address part, e.g. 'relay:[mx1.example.com]:25',
    or None if the response does not contain FILTER.
    """
    # response is e.g. "action=FILTER relay:[mx1.example.com]:25"
    if 'FILTER ' in response:
        return response.split('FILTER ', 1)[1].strip()
    return None


def main():
    config_data = load_config()
    server_addresses, group_addresses = build_group_addresses(config_data)
    test_config_path = create_test_config(config_data)
    server_proc = start_server(test_config_path)

    if server_proc.poll() is not None:
        stdout, stderr = server_proc.communicate()
        print("Server failed to start!")
        print(f"STDOUT: {stdout.decode('utf-8')}")
        print(f"STDERR: {stderr.decode('utf-8')}")
        os.remove(test_config_path)
        sys.exit(1)

    passed = 0
    failed = 0
    errors = []

    try:
        with open(ADDRESSES_PATH, 'r') as f:
            lines = f.readlines()

        print("\n--- Running Combined Rules Tests (YAML config) ---\n")

        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('sender'):
                continue

            parts = line.split('\t')
            if len(parts) < 3:
                print(f"  SKIP line {lineno}: not enough columns")
                continue

            sender = parts[0].strip()
            recipient = parts[1].strip()
            expected = parts[2].strip()

            try:
                valid = expected_addresses(expected, server_addresses, group_addresses)
            except ValueError as e:
                errors.append(f"Line {lineno}: {e}")
                failed += 1
                continue

            try:
                response = send_request(sender, recipient)
            except Exception as e:
                errors.append(f"Line {lineno}: connection error: {e}")
                failed += 1
                continue

            address = parse_response_address(response)

            if valid == 'DUNNO':
                roundrobin_enabled = config_data['config'].get('roundrobin', True)
                if roundrobin_enabled:
                    all_mxs = set(server_addresses.values())
                    if address and address in all_mxs:
                        passed += 1
                        print(f"  ✅ line {lineno}: {sender} -> {recipient}  "
                              f"expected=any_mx (roundrobin=true)  got={address}")
                    else:
                        failed += 1
                        msg = (f"  ❌ line {lineno}: {sender} -> {recipient}  "
                               f"expected=any_mx (roundrobin=true)  got={address!r}  "
                               f"raw={response!r}")
                        print(msg)
                        errors.append(msg)
                else:
                    if 'DUNNO' in response:
                        passed += 1
                        print(f"  ✅ line {lineno}: {sender} -> {recipient}  "
                              f"expected=DUNNO  got=DUNNO")
                    else:
                        failed += 1
                        msg = (f"  ❌ line {lineno}: {sender} -> {recipient}  "
                               f"expected=DUNNO  got={response!r}")
                        print(msg)
                        errors.append(msg)
            elif address and address in valid:
                passed += 1
                print(f"  ✅ line {lineno}: {sender} -> {recipient}  "
                      f"expected={expected}  got={address}")
            else:
                failed += 1
                msg = (f"  ❌ line {lineno}: {sender} -> {recipient}  "
                       f"expected={expected} (one of {valid})  got={address!r}  "
                       f"raw={response!r}")
                print(msg)
                errors.append(msg)

        print(f"\n--- Results: {passed} passed, {failed} failed ---")

        if errors:
            print("\nFailures:")
            for err in errors:
                print(f"  {err}")
            sys.exit(1)
        else:
            print("\n✅ All tests passed!")

    except Exception as e:
        print(f"\n--- Error: {e} ---")
        if server_proc.poll() is not None:
            stdout, stderr = server_proc.communicate()
            print(f"Server STDERR: {stderr.decode('utf-8')}")
        sys.exit(1)

    finally:
        server_proc.terminate()
        server_proc.wait()
        os.remove(test_config_path)


if __name__ == '__main__':
    main()
