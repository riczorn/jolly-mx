import yaml
import logging
import sys
import datetime

args = None

def log(message, to_stderr=False, needs_verbose=False):
    """Logs and flushes to stdout/stderr."""
    is_verbose = args.verbose if args and hasattr(args, 'verbose') else False
    is_quiet = args.quiet if args and hasattr(args, 'quiet') else False

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
        self.config = None
        self.servers = []
        self.logger = False

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

    def load(self, file_path):
        with open(file_path) as config_file:
            self.config_dict = yaml.safe_load(config_file)
            
            if 'config' not in self.config_dict or not self.config_dict['config']:
                self.config_dict['config'] = {}
                
            cfg = self.config_dict['config']
            cfg.setdefault('enabled', False)
            cfg.setdefault('log_file', '/var/log/jolly-mx.log')
            cfg.setdefault('csv_file', '/var/log/jolly-mx-messages.csv')
            cfg.setdefault('bind_host', '127.0.0.1')
            cfg.setdefault('bind_port', 9732)
            
            self.config = self.obj_dic(self.config_dict)
            
            log("# MX Servers", False, True)
            
            log_file = self.config.config.log_file
            self.csv_file = self.config.config.csv_file
                
            self.logger = self.setup_custom_logger('jolly-mx', log_file)
            self.servers_obj = Servers(self.config.servers.names)
            self.servers = self.servers_obj.servers
            
            # Create the server groups defined after servers.names in the configuration
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
        if not hasattr(self.config, rule_type): return False, False
        rules_dict = self.config_dict.get(rule_type, {})
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
        servers_obj = self.servers_obj

        if (identifier):
            server_groups = [sg for sg in vars(self.server_groups) if not sg.startswith('__')]
            if identifier in server_groups:
                servers_obj = getattr(self.server_groups, identifier)

        return servers_obj

    def test(self):
        for i in range(125000):
            self.servers_obj.get_next()
        self.servers_obj.print()

    def print_usage(self):
        log( "\nAll Servers", False, True )
        self.servers_obj.print()
        server_groups = [sg for sg in vars(self.server_groups) if not sg.startswith('__')]
        for server_name in server_groups:
            server_obj = self.get_server_group(server_name)
            log(f"\nGroup {server_name}", False, True)
            server_obj.print()

    def print_csv(self, sender, recipient, mx_group, mx_host):
        self.logger.info( f"{sender};{recipient};{mx_group};{mx_host}" )
        
        if hasattr(self, 'csv_file') and self.csv_file:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            csv_line = f"{now_str};{sender};{recipient};{mx_group};{mx_host}\n"
            try:
                with open(self.csv_file, 'a') as f:
                    f.write(csv_line)
            except Exception as e:
                log(f"ERROR: Failed to write to CSV log {self.csv_file} ({e})", False, False)
