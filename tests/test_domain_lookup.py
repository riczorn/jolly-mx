#!/usr/bin/env python3
import os
import sys
import yaml
import tempfile
import socket
import time
import subprocess
from unittest import mock

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
APP_PATH = os.path.join(PROJECT_DIR, 'jolly-mx.py')
PORT = 10104

def create_test_config():
    config_data = {
        'config': {
            'enabled': True,
            'roundrobin': False,
            'log_file': '/var/log/jolly-mx.log',
            'bind_host': '127.0.0.1',
            'bind_port': PORT,
            'verbose': True
        },
        'servers': {
            'names': {
                'mx_microsoft': {'address': 'relay:[mx.microsoft.example]:25'}
            },
            'groups': {
                'microsoft': ['mx_microsoft']
            }
        },
        'recipient_rules': {
            # Substring match in the MX record (not the recipient email domain itself)
            'protection.outlook.com': 'microsoft'
        }
    }
    
    fd, temp_path = tempfile.mkstemp(suffix='.yaml')
    with os.fdopen(fd, 'w') as f:
        yaml.dump(config_data, f)
        
    return temp_path

# Mock the dns.resolver.resolve function to return mock MX records
class MockExchange:
    def __init__(self, name):
        self.name = name
    def to_text(self):
        return self.name

class MockAnswer:
    def __init__(self, exchange):
        self.exchange = MockExchange(exchange)

def mock_resolve(domain, record_type):
    if domain == "microsoft.com":
        # MX record matches the 'protection.outlook.com' substring rule
        return [MockAnswer("microsoft-com.mail.protection.outlook.com.")]
    elif domain == "other.com":
        return [MockAnswer("mail.other.com.")]
    raise Exception("NXDOMAIN")

def test_domain_lookup():
    config_path = create_test_config()
    
    # We patch dns.resolver.resolve inline using a sneaky test setup:
    # Instead of monkeypatching the independent process, we can just load the module
    # and use get_mx_for_message directly! This makes it a unit test vs e2e.
    
    sys.path.insert(0, PROJECT_DIR)
    import importlib.util
    spec = importlib.util.spec_from_file_location("jolly_mx", APP_PATH)
    jmx = importlib.util.module_from_spec(spec)
    sys.modules["jolly_mx"] = jmx
    spec.loader.exec_module(jmx)
    jmx.config.config_file = config_path
    jmx.config.load()
    
    jmx.dns.resolver.resolve = mock_resolve
    
    print("\n--- Verifying Domain MX Lookup Rules ---")
    passed = 0
    failed = 0
    
    try:
        # Test 1: Domain whose MX record contains 'protection.outlook.com'
        print("Test 1: User at microsoft.com (MX has protection.outlook.com)")
        mx, group = jmx.get_mx_for_message("sender@example.com", "user@microsoft.com", 3600)
        print(f"  Response: mx={mx}, group={group}")
        if mx == "relay:[mx.microsoft.example]:25" and group == "microsoft":
            print("  ✅ Matched successfully")
            passed += 1
        else:
            print("  ❌ Failed match")
            failed += 1

        # Test 2: Domain whose MX record does NOT contain 'protection.outlook.com'
        print("\nTest 2: User at other.com (MX is mail.other.com)")
        mx, group = jmx.get_mx_for_message("sender@example.com", "user@other.com", 3600)
        print(f"  Response: mx={mx}, group={group}")
        if mx == False and group == "n/a":
            print("  ✅ Did not match as expected")
            passed += 1
        else:
            print("  ❌ Failed match")
            failed += 1

    except Exception as e:
        print(f"\n❌ Verification Failed: {e}")
        failed += 1
    finally:
        os.remove(config_path)

    print(f"\nResults: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)

if __name__ == '__main__':
    test_domain_lookup()
