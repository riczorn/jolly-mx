#!/usr/bin/env python3
"""
Postfix MX Smart Router Service - fasterweb.net
  a fork of postfix-mx-pattern-router which implements Weighted Round Robin
   but is incompatible with the original configuration.

- support for round-robin mx server groups
- each rule can target a specific group
- all servers are used if no group is chosen by a rule
- server groups have the same percentage usage as the main array. 
  keep this into consideration when choosing the percentage for the individual servers
- New configuration in yaml
    - server perc is the percentage out of 100 that this server should be chosen when a 
      mail targets that group and an mx address is returned
    - `default` allows you to specify a default group or NO RESULT;
       otherwise all servers are used. Please note `default` must be the first rule.
    - `default` = NO RESULT     returns status = 500
    - config.log_file must be writable

- on CTRL-C exit gracefully and show some stats such as : 

Group good
  Name          # Sent |  curr. % / target %
    mx1              5 |  41.6667 /  40.0000
    mx2              5 |  41.6667 /  40.0000
    mx3              2 |  16.6667 /  20.0000

Group bad
  Name          # Sent |  curr. % / target %
    mx4              1 | 100.0000 /  32.2581
    mx5              0 |   0.0000 /   3.2258
    mx6              0 |   0.0000 /  32.2581
    mx7              0 |   0.0000 /  32.2581

2025-10-03: published on github: https://github.com/riczorn/postfix-mx-smart-router
2025-10-05: added support for 500: NO RESULT
    - if a server identifier is used in a Rule, match it directly

    TODO
    - log DATE;from;to;result
    
See comments in the config sample file for more params explanations.

comment below is from the original code by filidorwiese
https://github.com/filidorwiese/postfix-mx-pattern-router
"""


"""
Postfix MX Pattern Router Service

This service acts as a TCP lookup table for Postfix to dynamically route emails based on
the MX records of the destination domain. It allows routing decisions to be made based on
pattern matching against MX hostnames.

Usage:
    python3 postfix-mx-pattern-router.py [options]

Options:
    -c, --config FILE    Path to configuration file (default: /etc/postfix/postfix-mx-pattern-router.conf)
    -p, --port PORT      Port to listen on (default: 10099)
    -H, --host HOST      Host to bind to (default: 127.0.0.1)
    --cache-ttl SEC      Cache TTL in seconds (default: 3600, where 0 disables cache)
    --timeout SEC        Client inactivity timeout in seconds (default: 30, where 0 disables timeout)
    -v, --verbose        Increase verbosity level of logging
    -q, --quiet          Disables logging except for errors

Configuration File Format:
    Each line should contain a pattern and a relay, separated by whitespace:
    pattern relay_transport

    Example:
    protection.outlook.com    relay:[office365-relay.example.com]:587
    mx.microsoft              relay:[office365-relay.example.com]:587
    icloud.com                relay:[icloud-relay.example.com]:587

Integration with Postfix:
    Add to /etc/postfix/main.cf:
    transport_maps = tcp:127.0.0.1:10099

    Then reload Postfix:
    systemctl reload postfix

Useful links:
 - https://www.postfix.org/transport.5.html
 - https://www.postfix.org/tcp_table.5.html
 - https://github.com/fbett/postfix-tcp-table-service
"""

import os
import sys
import signal
import socket
import re
import time
import dns.resolver
import urllib.parse
import argparse
import psutil
import threading
import yaml
import logging

# Change to the script's directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Default values
DEFAULT_PORT = 10099
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PATTERN_FILE = 'jolly-mx.yaml'
DEFAULT_CACHE_TTL = 3600
DEFAULT_CLIENT_TIMEOUT = 600
GC_INTERVAL = 3600
STATS_INTERVAL = 300

# In-memory cache for MX records
mx_cache = {}

# Global args variable
args = None

# Global counter for active connections
active_connections = 0


class Server:
    def __init__(self, name, address, perc_target = 100): 
        self.name = name
        self.address = address
        self.percent = perc_target  # 0..100 the initial required percentage, 
        """ the following two percentages are on the whole of the servers, hence it's divided (roughly) by 
            the number of servers (ns). This only is exactly the number of servers if all have the same percentage.
        """
        self.perc_target = 0    # 0..1/ns the percentage overall this single server aims to achieve
        self.perc_current = 0   # 0..1/ns the percentage achieved so far
        self.mails_sent = 0

class Servers:
    def __init__(self, server_list):
        self.servers = []
        self.current = -1
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

    def print(self):
        """ print the servers usage """
        self.calc_perc()
        usage = ""
        usage = f"  Name          # Sent |  curr. % / target %"
        for i in self.servers:
            usage = f"{usage}\n    {i.name:10s} {i.mails_sent:7,d} | {i.perc_current*100:8.4f} / {i.perc_target*100:8.4f}"
        log(usage, False, True)
        

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
        """ 
        iterates over servers, choosing the next one, whilst trying to have 
        each server send the right percentage of emails
        i.e. increment self.next by 1, until the server's current percentage 
        is lower than its target.
        the percentages are calculated each time from the totals calculated in calc_perc()

        identifier can be:
         - any of the good, bad arrays in the config (in which case it is ignored, 
           as this server group was already chosen)
         - a specific mx name, in which case it is simply returned.
        it defaults to all unless a `default` rule is present in the configuration.
        """
        chosen_server = False

        if mx_identifier:
            # this will find a server if its name is mx_identifier
            chosen_server = self.get(mx_identifier)

        if not chosen_server:
            # hence mx_identifier is a group
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
        # Server not found, most likely it's a group and will be handled by get_next
        return False
   

class Config:
    config = {}
    servers = []
    logger = False

    def setup_custom_logger(self, name, filename):
        logger = logging.getLogger(name)
        formatter = logging.Formatter(fmt='%(asctime)s;%(message)s',
                                    datefmt='%Y-%m-%d %H:%M:%S')
        logger.setLevel(logging.DEBUG)
        
        if filename:
            try:
                handler = logging.FileHandler(filename, mode='w')
                handler.setFormatter(formatter)
                logger.addHandler(handler)
            except:
                log(f"ERROR: Failed to setup file logger to {filename}", False, False);
                sys.exit(1) # Exit with error
        
        screen_handler = logging.StreamHandler(stream=sys.stdout)
        screen_handler.setFormatter(formatter)
        logger.addHandler(screen_handler)
        return logger
    

    def obj_dic(self, d):
        """
        Convert a dictionary into an object so instead of calling it with 
            config["group"]["attr"]
        I can use the syntax
            config.group.attr
        """
        top = type('new', (object,), d)
        seqs = tuple, list, set, frozenset
        for i, j in d.items():
            if isinstance(j, dict):
                setattr(top, i, self.obj_dic(j))
            elif isinstance(j, seqs):
                setattr(top, i, 
                    type(j)(obj_dic(sj) if isinstance(sj, dict) else sj for sj in j))
            else:
                setattr(top, i, j)
        return top


    def load(self, file_path):
        """
        Loads the configuration, no error checking is done yet. And no default values.
        If you omit a value, it may crash the program when you least expect it. 
        You have been warned.

        After turning the configuration into an object, it feeds said object to the 
        Servers to be created.

        See the .yaml file for reference.
        """
        with open(file_path) as config_file:
            self.config_dict = yaml.safe_load(config_file)
            self.config = self.obj_dic(self.config_dict)
            log("# MX Servers", False, True)
            log_file = False
            if hasattr(self.config, 'config') and hasattr(self.config.config, 'log_file'):
                log_file = self.config.config.log_file
                
            self.logger = self.setup_custom_logger('jolly-mx', log_file)
            self.servers_obj = Servers(self.config.servers.names)
            self.servers = self.servers_obj.servers
            
            #
            # Create the server groups defined after servers.names in the configuration
            #
            server_groups_names = [sg for sg in vars(self.config.servers) if not sg.startswith('__') and not sg=='names']
            server_groups = {} # object()
            for server_group_name in server_groups_names:
                server_group_list = getattr(self.config.servers, server_group_name)
                server_group_array = {}
                for server_name in server_group_list:
                    server_group_array[server_name] = getattr(self.config.servers.names, server_name)
                
                server_group_dict = self.obj_dic(server_group_array)
                log( f"# MX group           {server_group_name}", False, True )
                server_groups[server_group_name] = Servers(server_group_dict)
                
            self.server_groups = self.obj_dic (server_groups)
            log( f"Config.loaded\n", False, True )


    def test_domain_rules(self, email, domain, rule_type="sender_rules"):
        # domain should be like: 
        #   libero.it or mx.libero.it or mail.libero.it you get the gist
        if not hasattr(config.config, rule_type): return False, False
        rules_dict = config.config_dict.get(rule_type, {})
        rules = [r for r in rules_dict if not r.startswith('__')]
        
        default = False
        result = False
        for rule in rules:
            value = rules_dict[rule]
            if rule == "default":
                default = value
            if email == rule:
                result = value
                log( f"  Matched email {email} against {rule} in {rule_type}: {value}", False, True )
                break
            if rule in domain: # domain is the name of the mx record i.e mx.example.com
                result = value
                log( f"  Matched MX domain {domain} against {rule} in {rule_type}: {value}", False, True )
                break
            if rule in email: # this will match the rule "example.com" against john@example.com
                result = value
                log( f"  Matched mail domain {domain} against {rule} in {rule_type}: {value}", False, True )
                break
            
        if not result:
            result = default

        return result, default

    def get_server_group(self, identifier):
        """ 
        identifier can be either a server group or a server name; 
        in the latter case the full servers array will be returned, and get_next will spot the 
        specified server instead of iterating to the next available one
        """
        servers_obj = self.servers_obj

        if (identifier):
            server_groups = [sg for sg in vars(self.server_groups) if not sg.startswith('__')]
            if identifier in server_groups:
                servers_obj = getattr(self.server_groups, identifier)

        return servers_obj

    def test(self):
        """
        test run the weighted server round-robin
        """
        for i in range(125000):
            self.servers_obj.get_next()
        self.servers_obj.print()

    def print_usage(self):
        log( "\nAll Servers", False, True )
        self.servers_obj.print()
        server_groups = [sg for sg in vars(self.server_groups) if not sg.startswith('__')]
        for server_name in server_groups:
            server_obj = config.get_server_group(server_name)
            log(f"\nGroup {server_name}", False, True)
            server_obj.print()

    def print_log(self, sender, recipient, mx_group):
        self.logger.info( f"{sender};{recipient};{mx_group}" )


config = Config()






def custom_sigint_handler(sig, frame):
    """
    handle CTRL-C exit and other errors, and exits gracefully.
    """
    global config
    config.print_usage()
    print_stats()
    sys.exit(0) # Exit cleanly

# Register the handler for the SIGINT signal
signal.signal(signal.SIGINT, custom_sigint_handler)







def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Postfix MX Pattern Router Service + Round-Robin')
    parser.add_argument('-c', '--config',
                        default=DEFAULT_PATTERN_FILE,
                        help=f'Path to configuration file (default: {DEFAULT_PATTERN_FILE})')
    parser.add_argument('-p', '--port',
                        type=int,
                        default=DEFAULT_PORT,
                        help=f'Port to listen on (default: {DEFAULT_PORT})')
    parser.add_argument('-H', '--host',
                        default=DEFAULT_HOST,
                        help=f'Host to bind to (default: {DEFAULT_HOST})')
    parser.add_argument('--cache-ttl',
                        type=int,
                        default=DEFAULT_CACHE_TTL,
                        help=f'Cache TTL in seconds (default: {DEFAULT_CACHE_TTL}, where 0 disables cache)')
    parser.add_argument('--timeout',
                        type=int,
                        default=DEFAULT_CLIENT_TIMEOUT,
                        help=f'Client inactivity timeout in seconds (default: {DEFAULT_CLIENT_TIMEOUT}, where 0 disables timeout)')
    parser.add_argument('-v', '--verbose',
                        action='store_true',
                        default=False,
                        help=f'Increase verbosity level (default: false)')
    parser.add_argument('-q', '--quiet',
                        action='store_true',
                        default=False,
                        help=f'Quiet mode, disables logging (default: false)')
    return parser.parse_args()


def get_mx_records(domain, cache_ttl):
    """Get MX records for a domain using dns.resolver with optional caching.

    Returns:
        tuple: (mx_records, from_cache) where:
            - mx_records is a list of MX hostnames
            - from_cache is a boolean indicating if the result came from cache
    """
    current_time = time.time()

    # Check if caching is enabled (positive TTL) and we have a valid cached entry
    if cache_ttl > 0 and domain in mx_cache:
        cache_time, mx_records = mx_cache[domain]
        if current_time - cache_time < cache_ttl:
            return mx_records, True

    # No valid cache entry or caching disabled, perform DNS lookup
    try:
        answers = dns.resolver.resolve(domain, 'MX')
        mx_records = [answer.exchange.to_text().rstrip('.').lower() for answer in answers]

        # Cache the result if caching is enabled
        if cache_ttl > 0:
            mx_cache[domain] = (current_time, mx_records)

        return mx_records, False
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        # Cache empty result if caching is enabled
        if cache_ttl > 0:
            mx_cache[domain] = (current_time, [])

        return [], False


def cleanup_cache(cache_ttl):
    """Remove expired entries from the cache."""
    if cache_ttl <= 0:
        return 0  # Cache is disabled, nothing to clean up

    current_time = time.time()
    expired_keys = []

    # Identify expired entries
    for domain, (cache_time, _) in mx_cache.items():
        if current_time - cache_time >= cache_ttl:
            expired_keys.append(domain)

    # Remove expired entries
    for domain in expired_keys:
        del mx_cache[domain]

    if expired_keys:
        log(f"Garbage collection: removed {len(expired_keys)} expired cache entries, new total {len(mx_cache)}", False, True)

    return len(expired_keys)


def print_stats():
    process = psutil.Process(os.getpid())
    memory_usage = process.memory_info().rss / 1024 / 1024  # Convert to MB
    cache_size = len(mx_cache)
    log(f"Memory usage: {memory_usage:.2f} MB, Cache items: {cache_size}, Active connections: {active_connections}", False, True)


def jobs_thread():
    """Background thread function to periodically report stats and run garbage collection."""
    last_gc_time = time.time()

    while True:
        current_time = time.time()

        # Report stats
        print_stats()

        # Run garbage collection if cache is enabled and it's time
        if args.cache_ttl > 0 and current_time - last_gc_time >= GC_INTERVAL:
            cleanup_cache(args.cache_ttl)
            last_gc_time = current_time

        # Sleep until next interval
        time.sleep(STATS_INTERVAL)



def process_policy_request(request_data, conn, config, cache_ttl):
    sender = request_data.get('sender', '').lower()
    recipient = request_data.get('recipient', '').lower()

    mx, group = get_mx_for_message(sender, recipient, cache_ttl)
    
    # Log the decision to the specific log file
    config.print_log(sender, recipient, group)

    # Check config.enabled
    is_enabled = True
    if hasattr(config.config, 'config') and hasattr(config.config.config, 'enabled'):
        is_enabled = config.config.config.enabled

    if not is_enabled:
        action = "DUNNO"

    log(f"Policy Request -> Sender: {sender}, Recipient: {recipient} => Action: {action} (Enabled: {is_enabled}, MX Group: {group})", False, True)
    send_response(conn, action)


def get_mx_for_message(sender, recipient, cache_ttl):
    action = "DUNNO"
    group_matched = "n/a"
    
    # 1. Check recipient rules first
    if action == "DUNNO" and recipient:
        mx, group = get_next_server_for_email(recipient, cache_ttl, rule_type="recipient_rules")
        if mx and mx != "NO RESULT":
            action = f"FILTER {mx}"
            group_matched = group

    # 2. Check sender rules if no recipient rule matched
    if action == "DUNNO" and sender:
        mx, group = get_next_server_for_email(sender, cache_ttl, rule_type="sender_rules")
        if mx and mx != "NO RESULT":
            action = f"FILTER {mx}"
            group_matched = group

    return mx, group_matched

def send_response(conn, action):
    """Send a formatted policy response to Postfix."""
    response = f"action={action}\n\n"
    conn.sendall(response.encode('utf-8'))


def log(message, to_stderr=False, needs_verbose=False):
    """Logs and flushes to stdout/stderr."""
    if (to_stderr):
        sys.stderr.write(f"{message}\n")
    elif (needs_verbose and args.verbose) or not needs_verbose and not args.quiet:
        sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


def log_dict(dict, needs_verbose=False):
    for key, value in dict.items():
        log(f"  {key} → {value}", False, needs_verbose)


def handle_client(conn, addr, config, cache_ttl):
    """Handle a client connection in a separate thread."""
    global active_connections
    active_connections += 1

    try:
        # Set a timeout for client connections if enabled
        if args.timeout > 0:
            conn.settimeout(args.timeout)

        buffer = ""
        while True:
            data = conn.recv(1024)
            if not data:  # Connection closed by client
                log(f"Connection closed by client: {addr}", False, True)
                break

            buffer += data.decode('utf-8')
            
            # Postfix policy requests end with an empty line (\n\n)
            while "\n\n" in buffer:
                idx = buffer.find("\n\n")
                request_block = buffer[:idx]
                buffer = buffer[idx+2:]
                
                request_data = {}
                for line in request_block.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    if '=' in line:
                        key, val = line.split('=', 1)
                        request_data[key.strip()] = val.strip()

                try:
                    process_policy_request(request_data, conn, config, args.cache_ttl)
                except Exception as e:
                    log(f"Error processing request: {e}", True)
                    send_response(conn, "DUNNO")
                    break

    except Exception as e:
        if isinstance(e, socket.timeout):
            log(f"Connection timed out: {addr}", False, True)
        else:
            log(f"Error handling connection: {e}", True)
            try:
                send_response(conn, "DUNNO")
            except:
                pass

    finally:
        conn.close()
        active_connections -= 1



def get_next_server_for_email(email, cache_ttl, rule_type):
    global config
    mx_server_group = False
    default = False
    domain = email.split('@')[1] if '@' in email else ''

    if domain:
        mx_records, from_cache = get_mx_records(domain, cache_ttl)
        for mx in mx_records:
            mx_server_group, default = config.test_domain_rules(email, mx, rule_type=rule_type)
            if mx_server_group:
                break
        
        # If still not found via MX lookup, try matching directly on the email/domain
        if not mx_server_group:
            mx_server_group, default = config.test_domain_rules(email, domain, rule_type=rule_type)

    if mx_server_group == 'NO RESULT' and (default == 'NO RESULT' or not default):
        return "NO RESULT", mx_server_group

    if not mx_server_group:
        # No matching rule found
        return False, False
        
    servers_obj = config.get_server_group(mx_server_group)
    if not servers_obj:
        return False, mx_server_group

    mx = servers_obj.get_next(mx_server_group).address

    return mx, mx_server_group
    

def main():
    # Parse command line arguments
    global config
    global args
    args = parse_arguments()

    config_path = args.config
    if config_path == DEFAULT_PATTERN_FILE:
        # User didn't specify a custom path, let's check /etc/postfix/ first
        etc_path = f"/etc/postfix/{DEFAULT_PATTERN_FILE}"
        if os.path.exists(etc_path):
            config_path = etc_path
            
    # Load patterns from the specified configuration file
    config.load(config_path)
    config.test() # perform a few lookups to test the round robin

    found = False

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind((args.host, args.port))
        server.listen(5)
        if args.cache_ttl > 0:
            log(f"Socketmap server listening on {args.host}:{args.port} (cache {args.cache_ttl} seconds)")
        else:
            log(f"Socketmap server listening on {args.host}:{args.port} (no cache)")

        # Start a background thread for stats reporting and garbage collection
        background_thread = threading.Thread(target=jobs_thread, daemon=True)
        background_thread.start()

        while True:
            conn, addr = server.accept()
            client_thread = threading.Thread(
                target=handle_client,
                args=(conn, addr, config, args.cache_ttl),
                daemon=True
            )
            client_thread.start()

    except Exception as e:
        log(f"Failed to start server: {e}", True)
        sys.exit(1)

    finally:
        server.close()

if __name__ == "__main__":
    main()
