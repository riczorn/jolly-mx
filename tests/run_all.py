#!/usr/bin/env python3
import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

TEST_FILES = [
    "test_simple.py",
    "test_full.py",
    "test_rules.py",
    "test_domain_lookup.py",
    "test_improper_usage.py",
    "test_roundrobin.py",
    "test_direction.py",
    "test_auto_populate.py",
    "load_test.py",
    # "load_concurrent.py"
]

def run_all_tests():
    print(f"Running {len(TEST_FILES)} test suites...\n")
    
    all_passed = True
    failed_tests = []
    
    for test_file in TEST_FILES:
        test_path = os.path.join(SCRIPT_DIR, test_file)
        
        # print the running status on one line
        print(f"⌛ {test_file.ljust(30)}", end="", flush=True)
        
        try:
            result = subprocess.run(
                [sys.executable, test_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Go back to the beginning of the line to overwrite the hourglass
            print("\r", end="")
            
            if result.returncode == 0:
                print(f"✅ {test_file.ljust(30)}")
            else:
                print(f"❌ {test_file.ljust(30)}")
                failed_tests.append((test_file, result.stdout, result.stderr))
                all_passed = False
                
        except Exception as e:
            print("\r", end="")
            print(f"❌ {test_file.ljust(30)}")
            failed_tests.append((test_file, "", str(e)))
            all_passed = False

    if all_passed:
        print("\n🎉 All tests passed successfully!")
        sys.exit(0)
    else:
        print(f"\n💥 {len(failed_tests)} test(s) failed. Details:")
        for name, stdout, stderr in failed_tests:
            print(f"\n--- {name} ---")
            if stdout.strip():
                print("STDOUT:")
                print(stdout.strip())
            if stderr.strip():
                print("STDERR:")
                print(stderr.strip())
        sys.exit(1)

if __name__ == '__main__':
    run_all_tests()
