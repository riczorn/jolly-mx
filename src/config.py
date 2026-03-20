import yaml
import logging
import sys
import datetime
import threading

config = None

def log(message, to_stderr=False, needs_verbose=False):
    """Logs and flushes to stdout/stderr."""
    is_verbose = config.verbose if config else False
    is_quiet = config.quiet if config else False

    if to_stderr:
        sys.stderr.write(f"{message}\n")
    elif (needs_verbose and is_verbose) or not needs_verbose and not is_quiet:
        sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


class Server:
    def __init__(self, name, address, perc_target=100): 
        self.name = name
        self.address = address
        self.percent = perc_target  # 0..100 the initial required percentage, 
        """ the following two percentages are on the whole of the servers, hence it's divided (roughly) by 
            the number of servers (ns). It is divided exactly only if all servers have the same percentage.
        """
        self.perc_target = 0    # 0..1/ns the percentage overall this single server aims to achieve
        self.perc_current = 0   # 0..1/ns the percentage achieved so far
        self.mails_sent = 0

class Servers:
    def __init__(self, server_list):
        self.servers = []
        self.current = -1
        self.lock = threading.Lock()
        percent_sum = 0
        # build the main list of server names:
        for attr in vars(server_list):
            if not attr.startswith('__'):
                value = getattr(server_list, attr)
                if not hasattr(value, 'perc'):
                    value.perc = 100
                percent_sum += value.perc
                self.servers.append (Server(attr, value.address, value.perc))
                log (f"  {attr}: {value.address:20s} - {value.perc:4,d} %", False, True)

        # now I have the servers loaded: let's update perc_target to the global percentage.
        if len(self.servers)>0:
            for server in self.servers:
                server.perc_target = server.percent / percent_sum

    def print(self, logger=None):
        """ print the servers usage """
        self.calc_perc()
        usage = f"  Name          # Sent |  curr. % / target %"
        for i in self.servers:
            usage = f"{usage}\n    {i.name:10s} {i.mails_sent:7,d} | {i.perc_current*100:8.4f} / {i.perc_target*100:8.4f}"
        
        if logger:
            logger.info("\n" + usage) 
            
        return usage
        
    def calc_perc(self):
        """ 
        for each server, updated its current percentage
        """
        total_mails = 0
        for server in self.servers:
            total_mails += server.mails_sent
        if total_mails > 0:
            for server in self.servers:
                server.perc_current = server.mails_sent / total_mails

    def get_next(self, mx_identifier = False):
        with self.lock:
            chosen_server = False

            if mx_identifier:
                chosen_server = self.get(mx_identifier)

            if not chosen_server:
                current = (self.current + 1 ) % len(self.servers)
                self.calc_perc()
                
                found = False
                iteration = 0
                while iteration < len(self.servers) and not found:
                    iteration += 1
                    if self.servers[current].perc_current < self.servers[current].perc_target:
                        self.current = current
                        found = True
                        break
                    current = (current + 1 ) % len(self.servers)
                chosen_server = self.servers[self.current]

            chosen_server.mails_sent += 1
            return chosen_server

    def get(self, name):
        for server in self.servers:
            if name == server.name:
                return server
        return False


class Config:
    def __init__(self):
        self.config_dict = {}
        self.config_obj = None
        self.servers = []
        self.logger = False
        self.csv_buffer = []
        self.csv_lock = threading.Lock()
        self.csv_flush_thread = None
        self.enabled = False
        
        self.verbose = False
        self.quiet = False
        self.cache_ttl = 3600
        self.timeout = 600
        self.port = 9732
        self.host = '127.0.0.1'
        self.config_file = 'jolly-mx.yaml'
        self.parse_args()

    def setup_custom_logger(self, name, filename):
        logger = logging.getLogger(name)
        formatter = logging.Formatter(fmt='%(asctime)s;%(message)s',
                                    datefmt='%Y-%m-%d %H:%M:%S')
        logger.setLevel(logging.DEBUG)
        
        if filename:
            try:
                handler = logging.FileHandler(filename, mode='a')
                handler.setFormatter(formatter)
                logger.addHandler(handler)
            except Exception as e:
                log(f"ERROR: Failed to setup file logger to {filename} ({e})", False, False)
                sys.exit(1) # Exit with error
        
        screen_handler = logging.StreamHandler(stream=sys.stdout)
        screen_handler.setFormatter(formatter)
        logger.addHandler(screen_handler)
        return logger
    
    def obj_dic(self, d):
        top = type('new', (object,), d)
        seqs = tuple, list, set, frozenset
        for i, j in d.items():
            if isinstance(j, dict):
                setattr(top, i, self.obj_dic(j))
            elif isinstance(j, seqs):
                setattr(top, i, type(j)(self.obj_dic(sj) if isinstance(sj, dict) else sj for sj in j))
            else:
                setattr(top, i, j)
        return top

    def parse_args(self):
        import argparse
        parser = argparse.ArgumentParser(description='Postfix MX Pattern Router Service + Round-Robin')
        parser.add_argument('-c', '--config',
                            default=self.config_file,
                            help=f'Path to configuration file (default: {self.config_file})')
        parser.add_argument('-p', '--port',
                            type=int,
                            default=self.port,
                            help=f'Port to listen on (default: {self.port})')
        parser.add_argument('-H', '--host',
                            default=self.host,
                            help=f'Host to bind to (default: {self.host})')
        parser.add_argument('--cache-ttl',
                            type=int,
                            default=self.cache_ttl,
                            help=f'Cache TTL in seconds (default: {self.cache_ttl}, where 0 disables cache)')
        parser.add_argument('--timeout',
                            type=int,
                            default=self.timeout,
                            help=f'Client inactivity timeout in seconds (default: {self.timeout}, where 0 disables timeout)')
        parser.add_argument('-v', '--verbose',
                            action='store_true',
                            default=self.verbose,
                            help=f'Increase verbosity level (default: false)')
        parser.add_argument('-q', '--quiet',
                            action='store_true',
                            default=self.quiet,
                            help=f'Quiet mode, disables logging (default: false)')
        parsed_args = parser.parse_args()

        self.verbose = parsed_args.verbose
        self.quiet = parsed_args.quiet
        self.cache_ttl = parsed_args.cache_ttl
        self.timeout = parsed_args.timeout
        self.port = parsed_args.port
        self.host = parsed_args.host
        self.config_file = parsed_args.config

    def load(self):
        import os
        config_path = self.config_file
        if config_path == 'jolly-mx.yaml':
            etc_path = f"/etc/postfix/jolly-mx.yaml"
            if os.path.exists(etc_path):
                config_path = etc_path
        self.config_file = config_path

        if not os.path.exists(self.config_file):
            log(f"ERROR: Config file {self.config_file} not found", True)
            sys.exit(1)

        with open(self.config_file) as config_file:
            self.config_dict = yaml.safe_load(config_file)
            
            if 'config' not in self.config_dict or not self.config_dict['config']:
                self.config_dict['config'] = {}
                
            cfg = self.config_dict['config']
            self.enabled = cfg.get('enabled', False)
            self.log_file = cfg.get('log_file', '/var/log/jolly-mx.log')
            self.csv_file = cfg.get('csv_file', '/var/log/jolly-mx-messages.csv')
            
            if cfg.get('debug', False):
                self.verbose = True
            
            bind_host = cfg.get('bind_host', '127.0.0.1')
            bind_port = int(cfg.get('bind_port', 9732))
            
            if self.host == '127.0.0.1' and bind_host:
                self.host = bind_host
            if self.port == 9732 and bind_port:
                self.port = bind_port
            
            self.config_obj = self.obj_dic(self.config_dict)
            
            log("# MX Servers", False, True)
                
            self.logger = self.setup_custom_logger('jolly-mx', self.log_file)
            
            self.server_groups = self.obj_dic({})
            
            if hasattr(self.config_obj, 'servers') and hasattr(self.config_obj.servers, 'names'):
                self.servers_obj = Servers(self.config_obj.servers.names)
                self.servers = self.servers_obj.servers
                
                # Create the server groups defined after servers.names in the configuration
                server_groups_names = [sg for sg in vars(self.config_obj.servers) if not sg.startswith('__') and not sg=='names']
                server_groups = {} # object()
                for server_group_name in server_groups_names:
                    server_group_list = getattr(self.config_obj.servers, server_group_name)
                    server_group_array = {}
                    for server_name in server_group_list:
                        server_group_array[server_name] = getattr(self.config_obj.servers.names, server_name)
                    
                    server_group_dict = self.obj_dic(server_group_array)
                    log( f"# MX group           {server_group_name}", False, True )
                    server_groups[server_group_name] = Servers(server_group_dict)
                    
                self.server_groups = self.obj_dic(server_groups)

            # Load combined rules
            self.combined_rule_groups = self.obj_dic({})
            if 'combined_rules' in self.config_dict and self.config_dict['combined_rules']:
                log("# Combined Rules", False, True)
                combined_rules = {}
                for combined_key, server_list in self.config_dict['combined_rules'].items():
                    log(f"  {combined_key}: {server_list}", False, True)
                    # If the value is a group name (string), resolve it to the group's server list
                    if isinstance(server_list, str):
                        servers_section = self.config_dict.get('servers', {})
                        if server_list in servers_section:
                            server_list = servers_section[server_list]
                        else:
                            log(f"WARNING: Combined rule '{combined_key}' references unknown group '{server_list}'", True)
                            continue
                    # Create a Servers object from the list of server names
                    server_group_array = {}
                    for server_name in server_list:
                        if hasattr(self.config_obj.servers.names, server_name):
                            server_group_array[server_name] = getattr(self.config_obj.servers.names, server_name)
                        else:
                            log(f"WARNING: Combined rule '{combined_key}' references unknown server '{server_name}'", True)
                    
                    if server_group_array:
                        server_group_dict = self.obj_dic(server_group_array)
                        combined_rules[combined_key] = Servers(server_group_dict)
                
                self.combined_rule_groups = self.obj_dic(combined_rules)

            log( f"Config.loaded\n", False, True )

    def test_domain_rules(self, email, domain, rule_type="sender_rules"):
        if not hasattr(self.config_obj, rule_type): return False, False
        rules_dict = self.config_dict.get(rule_type, {})
        rules = [r for r in rules_dict if not r.startswith('__')]
        
        default = False
        result = False
        for rule in rules:
            value = rules_dict[rule]
            if rule == "default":
                default = value
                continue
                
            matched = False
            if '@' in rule:
                # Email rule: exact match only
                matched = (email == rule)
            elif rule in domain:
                # MX domain match (substring of the MX record)
                matched = True
            elif domain == rule or domain.endswith('.' + rule):
                # Domain suffix match
                matched = True

            if matched:
                result = value
                match_type = f"email {email}" if '@' in rule else f"MX domain {domain}" if rule in domain else f"mail domain {domain}"
                log( f"  Matched {match_type} against {rule} in {rule_type}: {value}", False, True )
                break

        if not result:
            result = default

        return result, default

    def get_server_group(self, identifier):
        servers_obj = self.servers_obj

        if (identifier):
            server_groups = [sg for sg in vars(self.server_groups) if not sg.startswith('__')]
            if identifier in server_groups:
                servers_obj = getattr(self.server_groups, identifier)
            else:
                log(f"WARNING: Unknown server group '{identifier}', using full server pool", True)

        return servers_obj

    def print_usage(self):
        output = "\nAll Servers\n"
        if self.logger: self.logger.info("All Servers")
        output += self.servers_obj.print(self.logger)
        
        server_groups = [sg for sg in vars(self.server_groups) if not sg.startswith('__')]
        for server_name in server_groups:
            server_obj = self.get_server_group(server_name)
            
            output += f"\n\nGroup {server_name}\n"
            if self.logger: self.logger.info(f"Group {server_name}")
            output += server_obj.print(self.logger)
            
        return output

    def print_csv(self, sender, recipient, mx_group, mx_host):
        if hasattr(self, 'csv_file') and self.csv_file:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            csv_line = f"{now_str};{sender};{recipient};{mx_group};{mx_host}\n"
            with self.csv_lock:
                self.csv_buffer.append(csv_line)

    def flush_csv(self):
        """Write all buffered CSV lines to disk."""
        if not hasattr(self, 'csv_file') or not self.csv_file:
            return
        with self.csv_lock:
            if not self.csv_buffer:
                return
            lines = self.csv_buffer[:]
            self.csv_buffer.clear()
        try:
            with open(self.csv_file, 'a') as f:
                f.writelines(lines)
        except Exception as e:
            log(f"ERROR: Failed to write to CSV log {self.csv_file} ({e})", False, False)

    def start_csv_flush_thread(self):
        """Start a daemon thread that flushes the CSV buffer every 10 seconds."""
        def _flush_loop():
            while True:
                import time
                time.sleep(10)
                self.flush_csv()
        self.csv_flush_thread = threading.Thread(target=_flush_loop, daemon=True)
        self.csv_flush_thread.start()
