import yaml
import logging
import sys
import datetime
import threading
import ipaddress

config = None

def log(message, to_stderr=False):
    """Operational console output (startup, errors, warnings). Always shown."""
    if to_stderr:
        sys.stderr.write(f"{message}\n")
        sys.stderr.flush()
    else:
        sys.stdout.write(f"{message}\n")
        sys.stdout.flush()

def log_debug(message):
    """Console output only when verbose."""
    if config.verbose:
        sys.stdout.write(f"{message}\n")
        sys.stdout.flush()

def log_to_file(message):
    """Write to log file only, never to console."""
    if config.logger:
        config.logger.info(message)

def log_request(sender, recipient, group, mx, action, request_data=None, direction="", client_address="", sasl_username=""):
    """Per-request output to console and log file.
    
    Console: always shows summary line; verbose adds postfix payload.
    Log file: verbose only — payload + summary.
    """
    summary = f"{sender}\t{recipient}\t{group}\t{mx}\t{action}\t{client_address}"
    if direction:
        summary += f"\t{direction}"
    if sasl_username and sasl_username != sender:
        summary += f"\t(sasl:{sasl_username})"

    if config.verbose and request_data:
        # Console: show postfix payload + summary
        payload = "\n".join(f"  {k}={v}" for k, v in request_data.items())
        sys.stdout.write(f"{payload}\n{summary}\n")
        sys.stdout.flush()
        # Log file: payload + summary
        log_to_file(f"{payload}\n{summary}")
    else:
        # Console: summary only
        sys.stdout.write(f"{summary}\n")
        sys.stdout.flush()


class Server:
    def __init__(self, name, address, weight_target=100): 
        self.name = name
        self.address = address
        self.weight = weight_target  # 0..100 the initial required percentage, 
        """ the following two percentages are on the whole of the servers, hence it's divided (roughly) by 
            the number of servers (ns). It is divided exactly only if all servers have the same percentage.
        """
        self.weight_target = 0    # 0..1/ns the percentage overall this single server aims to achieve
        self.weight_current = 0   # 0..1/ns the percentage achieved so far
        self.mails_sent = 0

class Servers:
    def __init__(self, server_list):
        self.servers = []
        self.current = -1
        self.lock = threading.Lock()
        weight_sum = 0
        # build the main list of server hosts:
        for attr in vars(server_list):
            if not attr.startswith('__'):
                value = getattr(server_list, attr)
                if not hasattr(value, 'weight'):
                    value.weight = 100
                weight_sum += value.weight
                self.servers.append (Server(attr, value.address, value.weight))
                log_debug(f"  {attr}: {value.address:20s} - {value.weight:4,d} %")

        # now I have the servers loaded: let's update weight_target to the global percentage.
        if len(self.servers)>0:
            for server in self.servers:
                server.weight_target = server.weight / weight_sum

    def print(self):
        """ print the servers usage """
        self.calc_weight()
        usage = f"  Name          # Sent |  curr. % / target %"
        for i in self.servers:
            usage = f"{usage}\n    {i.name:10s} {i.mails_sent:7,d} | {i.weight_current*100:8.4f} / {i.weight_target*100:8.4f}"
            
        return usage
        
    def calc_weight(self):
        """ 
        for each server, updated its current percentage
        """
        total_mails = 0
        for server in self.servers:
            total_mails += server.mails_sent
        if total_mails > 0:
            for server in self.servers:
                server.weight_current = server.mails_sent / total_mails

    def get_next(self, mx_identifier = False):
        with self.lock:
            chosen_server = False

            if mx_identifier:
                chosen_server = self.get(mx_identifier)

            if not chosen_server:
                current = (self.current + 1 ) % len(self.servers)
                self.calc_weight()
                
                found = False
                iteration = 0
                while iteration < len(self.servers) and not found:
                    iteration += 1
                    if self.servers[current].weight_current < self.servers[current].weight_target:
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
        global config
        config = self
        self.config_dict = {}
        self.config_obj = None
        self.servers = []
        self.logger = False
        self.csv_file = None
        self.csv_buffer = []
        self.csv_lock = threading.Lock()
        self.csv_flush_thread = None
        self.enabled = False
        self.reject_sender_login_mismatch = False
        self.allowed_ips = set()  # resolved set of allowed IPs (empty = allow all)
        self.local_networks = []
        self.local_domains = []
        
        self.verbose = False
        self.cache_ttl = 3600
        self.timeout = 600
        self.port = 9732
        self.host = '127.0.0.1'
        self.config_file = 'jolly-mx.yaml'
        self.auto_populate_local_domains = False
        self.postfix_virtual_file = ''
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
                log(f"ERROR: Failed to setup file logger to {filename} ({e})", to_stderr=True)
                sys.exit(1) # Exit with error
        
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
        parsed_args = parser.parse_args()

        self.verbose = parsed_args.verbose
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
            try:
                self.config_dict = yaml.safe_load(config_file)
            except yaml.YAMLError as exc:
                log(f"ERROR: Failed to parse YAML configuration file {self.config_file}:\n  {exc}", True)
                sys.exit(1)
                
            if not isinstance(self.config_dict, dict):
                log(f"ERROR: Configuration file {self.config_file} is empty or not formatted correctly as a YAML dictionary.", True)
                sys.exit(1)
            
            if 'config' not in self.config_dict or not self.config_dict['config']:
                self.config_dict['config'] = {}
                
            cfg = self.config_dict['config']
            self.enabled = cfg.get('enabled', self.enabled)
            self.reject_sender_login_mismatch = cfg.get('reject_sender_login_mismatch', self.reject_sender_login_mismatch)
            self.log_file = cfg.get('log_file', '/var/log/jolly-mx.log')
            self.csv_file = cfg.get('csv_file', '/var/log/jolly-mx-messages.csv')
            self.verbose = cfg.get('verbose', self.verbose)
            
            # Resolve allowed_clients to a set of IPs
            allowed_clients = cfg.get('allowed_clients', [])
            self.allowed_ips = self._resolve_allowed_clients(allowed_clients)
            
            local_networks = cfg.get('local_networks', ['127.0.0.0/8'])
            for net in local_networks:
                try:
                    self.local_networks.append(ipaddress.ip_network(net, strict=False))
                except ValueError as e:
                    log(f"WARNING: Invalid local network '{net}': {e}", to_stderr=True)
            self.local_domains = [str(d).lower() for d in cfg.get('local_domains', [])]
            
            self.auto_populate_local_domains = cfg.get('auto_populate_local_domains', False)
            self.postfix_virtual_file = cfg.get('postfix_virtual_file', '')
            self.populate_local_domains()
            
            bind_host = cfg.get('bind_host', '127.0.0.1')
            bind_port = int(cfg.get('bind_port', 9732))
            
            if self.host == '127.0.0.1' and bind_host:
                self.host = bind_host
            if self.port == 9732 and bind_port:
                self.port = bind_port
            
            self.config_obj = self.obj_dic(self.config_dict)
            
                
            self.logger = self.setup_custom_logger('jolly-mx', self.log_file)
            
            log_debug("# MX Servers")
            
            self.server_groups = self.obj_dic({})
            
            if hasattr(self.config_obj, 'servers') and hasattr(self.config_obj.servers, 'hosts'):
                self.servers_obj = Servers(self.config_obj.servers.hosts)
                self.servers = self.servers_obj.servers
                
                # Create the server groups defined under servers.groups in the configuration
                groups_dict = self.config_dict.get('servers', {}).get('groups', {})
                server_groups = {}
                for server_group_name, server_group_list in groups_dict.items():
                    server_group_array = {}
                    for server_name in server_group_list:
                        server_group_array[server_name] = getattr(self.config_obj.servers.hosts, server_name)
                    
                    server_group_dict = self.obj_dic(server_group_array)
                    log_debug(f"# MX group           {server_group_name}")
                    server_groups[server_group_name] = Servers(server_group_dict)
                    
                self.server_groups = self.obj_dic(server_groups)
                
                # Load servers.default configuration
                self.servers_default_obj = None
                self.servers_default_action = "DUNNO"
                
                default_val = self.config_dict.get('servers', {}).get('default', 'ALL')
                if isinstance(default_val, list):
                    # Array of servers [mx1,mx2,mx3]
                    server_group_array = {}
                    for server_name in default_val:
                        if hasattr(self.config_obj.servers.hosts, server_name):
                            server_group_array[server_name] = getattr(self.config_obj.servers.hosts, server_name)
                        else:
                            log(f"WARNING: servers.default references unknown server '{server_name}'", to_stderr=True)
                    if server_group_array:
                        self.servers_default_obj = Servers(self.obj_dic(server_group_array))
                elif isinstance(default_val, str):
                    if default_val == "ALL":
                        self.servers_default_obj = self.servers_obj
                    elif default_val in server_groups:
                        self.servers_default_obj = server_groups[default_val]
                    else:
                        self.servers_default_action = "DUNNO"

            # Load combined rules
            self.combined_rule_groups = {}
            if 'combined_rules' in self.config_dict and self.config_dict['combined_rules']:
                log_debug("# Combined Rules")
                combined_rules = {}
                for combined_key, server_list in self.config_dict['combined_rules'].items():
                    log_debug(f"  {combined_key}: {server_list}")
                    # If the value is a group name (string), resolve it to the group's server list
                    if isinstance(server_list, str):
                        groups_section = self.config_dict.get('servers', {}).get('groups', {})
                        if server_list in groups_section:
                            server_list = groups_section[server_list]
                        else:
                            log(f"WARNING: Combined rule '{combined_key}' references unknown group '{server_list}'", to_stderr=True)
                            continue
                    # Create a Servers object from the list of server names
                    server_group_array = {}
                    for server_name in server_list:
                        if hasattr(self.config_obj.servers.hosts, server_name):
                            server_group_array[server_name] = getattr(self.config_obj.servers.hosts, server_name)
                        else:
                            log(f"WARNING: Combined rule '{combined_key}' references unknown server '{server_name}'", to_stderr=True)
                    
                    if server_group_array:
                        server_group_dict = self.obj_dic(server_group_array)
                        combined_rules[combined_key] = Servers(server_group_dict)
                    
                if combined_rules:
                    self.combined_rule_groups = combined_rules

            log_debug("Config.loaded\n")

    def populate_local_domains(self):
        if self.auto_populate_local_domains:
            import os
            if os.path.exists(self.postfix_virtual_file):
                try:
                    with open(self.postfix_virtual_file, 'r') as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith('#') or '@' in line:
                                continue
                            parts = line.split()
                            if parts:
                                domain = parts[0].lower()
                                if domain not in self.local_domains:
                                    self.local_domains.append(domain)
                    log_debug(f"# Auto-populated local_domains from {self.postfix_virtual_file}")
                except Exception as e:
                    log(f"WARNING: Failed to read postfix_virtual_file {self.postfix_virtual_file}: {e}", to_stderr=True)
            else:
                log(f"WARNING: postfix_virtual_file {self.postfix_virtual_file} not found for auto-population.", to_stderr=True)

    def test_domain_rules(self, email, domain, rule_type="sender_rules"):
        if not hasattr(self.config_obj, rule_type): return False, False
        rules_dict = self.config_dict.get(rule_type) or {}
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
                log_debug(f"  Matched {match_type} against {rule} in {rule_type}: {value}")
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
                log(f"WARNING: Unknown server group '{identifier}', using full server pool", to_stderr=True)

        return servers_obj

    def _resolve_allowed_clients(self, hosts):
        """Resolve a list of hostnames/IPs to a set of IP addresses."""
        import socket as _socket
        if not hosts:
            return set()
        resolved = set()
        for host in hosts:
            host = str(host).strip()
            if not host or host == '0.0.0.0':
                return set()  # 0.0.0.0 means allow all
            try:
                # getaddrinfo handles IPv4, IPv6, and DNS names
                results = _socket.getaddrinfo(host, None)
                for family, _type, _proto, _canonname, sockaddr in results:
                    resolved.add(sockaddr[0])
            except _socket.gaierror:
                log(f"WARNING: Could not resolve allowed_host '{host}'", to_stderr=True)
        if resolved:
            log_debug(f"# Allowed hosts: {resolved}")
        return resolved

    def is_allowed(self, addr_ip):
        """Check if an IP address is in the allowed set. Empty set = allow all."""
        if not self.allowed_ips:
            return True
        return addr_ip in self.allowed_ips

    def is_local_client(self, ip_str):
        if not ip_str:
            return False
        try:
            ip = ipaddress.ip_address(ip_str)
            for net in self.local_networks:
                if ip in net:
                    return True
        except ValueError:
            pass
        return False

    def is_local_domain(self, domain):
        if not domain:
            return False
        # always accept if local_domains is not set.
        if len(self.local_domains) == 0:
            return True

        # accept domain only if it is in the local_domains list
        domain = domain.lower()
        for local_dom in self.local_domains:
            if domain == local_dom or domain.endswith('.' + local_dom):
                return True
        return False

    def print_usage(self):
        output = "\nAll Servers\n"
        output += self.servers_obj.print()
        
        server_groups = [sg for sg in vars(self.server_groups) if not sg.startswith('__')]
        for server_name in server_groups:
            server_obj = self.get_server_group(server_name)
            
            output += f"\n\nGroup {server_name}\n"
            output += server_obj.print()
            
        log_to_file(output)
        return output

    def print_csv(self, sender, recipient, mx_group, mx_host, direction="", client_address="", sasl_username=""):
        if self.csv_file:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            csv_line = f"{now_str};{sender};{recipient};{mx_group};{mx_host};{client_address};{direction}"
            sasl_info = f"sasl:{sasl_username}" if (sasl_username and sasl_username != sender) else ""
            csv_line += f";{sasl_info}\n"
            with self.csv_lock:
                self.csv_buffer.append(csv_line)

    def flush_csv(self):
        """Write all buffered CSV lines to disk."""
        if not self.csv_file:
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
            log(f"ERROR: Failed to write to CSV log {self.csv_file} ({e})", to_stderr=True)

    def start_csv_flush_thread(self):
        """Start a daemon thread that flushes the CSV buffer every 10 seconds."""
        def _flush_loop():
            while True:
                import time
                time.sleep(10)
                self.flush_csv()
        self.csv_flush_thread = threading.Thread(target=_flush_loop, daemon=True)
        self.csv_flush_thread.start()
