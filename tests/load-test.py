import os
import sys

# Add the parent directory to the path so we can import src.config
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import src.config as cfg

def main():
    config = cfg.Config()
    
    # Check if config file exists
    config_path = 'jolly-mx.yaml'
    if not os.path.exists(config_path):
        config_path = 'jolly-mx.yaml.example'
        if not os.path.exists(config_path):
            print("Error: Could not find jolly-mx.yaml or jolly-mx.yaml.example")
            sys.exit(1)
    # Bypass CLI args mock requirement
    config.verbose = True
    config.quiet = False
    config.config_file = config_path

    config.load()
    
    print("Running load test for 125,000 requests...")
    for i in range(125000):
        config.servers_obj.get_next()
        
    config.servers_obj.print()
    print("-------------")
    print(config.print_usage())

if __name__ == '__main__':
    main()
