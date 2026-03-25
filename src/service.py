"""
PolicyService: the TCP server, connection handler, and supporting system functions.

Wraps all infrastructure concerns (socket handling, caching, stats,
signal handling, input validation) so that jolly-mx.py only contains
the mail routing business logic.
"""

import os
import sys
import re
import time
import socket
import signal
import threading
import psutil

from src.config import log, log_debug, log_to_file, log_request

GC_INTERVAL = 3600
STATS_INTERVAL = 300
MAX_REQUEST_SIZE = 10240  # 10 KB max for a single policy request
EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+=\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


class PolicyService:
    """TCP server that handles Postfix policy delegation requests."""

    def __init__(self, config, request_handler):
        """
        Args:
            config: the Config instance
            request_handler: callable(sender, recipient, cache_ttl) -> (mx, group)
                The business logic function for routing messages
                implemented in jolly-mx.py:get_mx_for_message()
        """
        self.config = config
        self.request_handler = request_handler

        # In-memory cache for MX records
        self.mx_cache = {}
        self.cache_lock = threading.Lock()

        # Active connection counter
        self.active_connections = 0
        self.connections_lock = threading.Lock()

    # ── Stats ────────────────────────────────────────────────────────

    def print_stats(self):
        process = psutil.Process(os.getpid())
        memory_usage = process.memory_info().rss / 1024 / 1024
        with self.cache_lock:
            cache_size = len(self.mx_cache)
        return f"Memory usage: {memory_usage:.2f} MB, Cache items: {cache_size}, Active connections: {self.active_connections}"

    # ── Cache ────────────────────────────────────────────────────────

    def cleanup_cache(self):
        """Remove expired entries from the cache."""
        cache_ttl = self.config.cache_ttl
        if cache_ttl <= 0:
            return 0

        current_time = time.time()
        expired_keys = []

        with self.cache_lock:
            for domain, (cache_time, _) in self.mx_cache.items():
                if current_time - cache_time >= cache_ttl:
                    expired_keys.append(domain)

            for domain in expired_keys:
                del self.mx_cache[domain]

        if expired_keys:
            log_debug(f"Garbage collection: removed {len(expired_keys)} expired cache entries, new total {len(self.mx_cache)}")

        return len(expired_keys)

    # ── Background jobs ──────────────────────────────────────────────

    def jobs_thread(self):
        """Background thread: periodic stats reporting and garbage collection."""
        last_gc_time = time.time()

        while True:
            current_time = time.time()
            log_debug(self.print_stats())

            if self.config.cache_ttl > 0 and current_time - last_gc_time >= GC_INTERVAL:
                self.cleanup_cache()
                last_gc_time = current_time

            time.sleep(STATS_INTERVAL)

    # ── Input validation ─────────────────────────────────────────────

    @staticmethod
    def validate_request(request_data, raw_size):
        """Validate a policy request. Returns (ok, reason)."""
        if raw_size > MAX_REQUEST_SIZE:
            return False, f"Request too large ({raw_size} bytes, max {MAX_REQUEST_SIZE})"

        if 'protocol_name' not in request_data:
            return False, "Missing required field: protocol_name"

        sender = request_data.get('sender', '')
        recipient = request_data.get('recipient', '')

        if not sender:
            return False, "Missing required field: sender"
        if not recipient:
            return False, "Missing required field: recipient"

        if not EMAIL_RE.match(sender):
            return False, f"Invalid sender address: {sender!r}"
        if not EMAIL_RE.match(recipient):
            return False, f"Invalid recipient address: {recipient!r}"

        return True, None

    # ── Request processing ───────────────────────────────────────────

    @staticmethod
    def send_response(conn, action):
        """Send a formatted policy response to Postfix."""
        response = f"action={action}\n\n"
        conn.sendall(response.encode('utf-8'))

    def process_policy_request(self, request_data, conn):
        """Process a single policy request: route, log, respond."""
        sender = request_data.get('sender', '').lower()
        recipient = request_data.get('recipient', '').lower()
        sasl_username = request_data.get('sasl_username', '')
        client_address = request_data.get('client_address', '')
        
        recipient_domain = recipient.split('@')[-1] if '@' in recipient else recipient

        mail_direction = ""
        action = "DUNNO"
        mx_host = "n/a"
        group = "n/a"
        
        # Determine direction
        if sasl_username:
            mail_direction = "OUTGOING"
        elif self.config.is_local_client(client_address):
            mail_direction = "OUTGOING"
        elif self.config.is_local_domain(recipient_domain):
            mail_direction = "INCOMING"
        else:
            # Open relay attempt!
            if self.config.enabled:
                action = "REJECT OPEN RELAY ATTEMPT"
            else:
                action = "DUNNO"
            
            self.config.print_csv(sender, recipient, "OPEN_RELAY", "n/a", direction="REJECTED", client_address=client_address, sasl_username=sasl_username)
            log_request(sender, recipient, "OPEN_RELAY", "n/a", action, request_data, direction="REJECTED", client_address=client_address, sasl_username=sasl_username)
            self.send_response(conn, action)
            return

        if self.config.reject_sender_login_mismatch and mail_direction == "OUTGOING" and sasl_username and sasl_username != sender:
            action = f"REJECT Sender address {sender} does not match login {sasl_username}"
            if not self.config.enabled:
                action = "DUNNO"
                
            self.config.print_csv(sender, recipient, "REJECT_LOGIN_MISMATCH", "n/a", direction="REJECTED", client_address=client_address, sasl_username=sasl_username)
            log_request(sender, recipient, "REJECT_LOGIN_MISMATCH", "n/a", action, request_data, direction="REJECTED", client_address=client_address, sasl_username=sasl_username)
            self.send_response(conn, action)
            return

        if mail_direction == "OUTGOING":
            # invoke jolly-mx.py:get_mx_for_message()
            mx, group = self.request_handler(sender, recipient, self.config.cache_ttl)

            if mx == "NO RESULT" or not mx:
                action = "DUNNO"
            elif mx.split()[0] in ["DUNNO", "REJECT", "DEFER", "HOLD", "DISCARD"] or mx.startswith(("4", "5")):
                action = mx
            else:
                action = f"FILTER {mx}"

            mx_host = mx if mx else "n/a"

            if not self.config.enabled:
                action = "DUNNO"
        elif mail_direction == "INCOMING":
            action = "DUNNO"

        # other things we could return:
        #   action = "500 show the reason for the generic error"
        #   action = "554 state the reason for blocking"
        # or alternatively:
        #   action = "REJECT Blacklisted IP"
        #
        #   action = "DEFER I'm tired now"      # ask the server to retry later
        #   action = "HOLD some optional text"  # fool the spammer that the mail was accepted
        #                                       # and instead put it on hold
        #   action = "DISCARD <optional text>"  # discard and tell the spammer we accepted it"
        #   action = "PREPEND <Header-Name: Header-Value>" # prepend a header to the email
        #           e.g. action=PREPEND X-MyPolicy-Result: Pass
        #   action = "FILTER mx1.example.com"   # send the mail to mx1.example.com
        
        # if action=="DUNNO" and self.config.roundrobin:
        #     mx = self.config.servers.get_next()
        #     action = f"FILTER {mx}"         

        self.config.print_csv(sender, recipient, group, mx_host, direction=mail_direction, client_address=client_address, sasl_username=sasl_username)
        log_request(sender, recipient, group, mx_host, action, request_data, direction=mail_direction, client_address=client_address, sasl_username=sasl_username)

        self.send_response(conn, action)

    # ── Connection handler ───────────────────────────────────────────

    def handle_client(self, conn, addr):
        """Handle a client connection in a separate thread."""
        with self.connections_lock:
            self.active_connections += 1

        try:
            if self.config.timeout > 0:
                conn.settimeout(self.config.timeout)

            buffer = ""
            while True:
                data = conn.recv(1024)
                if not data:
                    log_debug(f"Connection closed by client: {addr}")
                    break

                buffer += data.decode('utf-8')

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

                    ok, reason = self.validate_request(request_data, len(request_block))
                    if not ok:
                        log(f"Invalid request from {addr}: {reason}\n{request_data}", to_stderr=True)
                        log_to_file(f"Invalid request from {addr}: {reason}")
                        self.send_response(conn, "DUNNO")
                        continue

                    try:
                        self.process_policy_request(request_data, conn)
                    except Exception as e:
                        log(f"Error processing request: {e}", to_stderr=True)
                        self.send_response(conn, "DUNNO")
                        break

        except Exception as e:
            if isinstance(e, socket.timeout):
                log_debug(f"Connection timed out: {addr}")
            else:
                log(f"Error handling connection: {e}", to_stderr=True)
                try:
                    self.send_response(conn, "DUNNO")
                except:
                    pass

        finally:
            conn.close()
            with self.connections_lock:
                self.active_connections -= 1

    # ── Signal handlers ──────────────────────────────────────────────

    def _shutdown(self):
        """Graceful shutdown: flush CSV, print stats, exit."""
        self.config.verbose = True
        self.config.flush_csv()
        log(self.config.print_usage())
        log(self.print_stats())
        sys.exit(0)

    def register_signals(self):
        """Register SIGINT and SIGTERM handlers."""
        signal.signal(signal.SIGINT, lambda _s, _f: self._shutdown())
        signal.signal(signal.SIGTERM, lambda _s, _f: self._shutdown())

    # ── Main server loop ─────────────────────────────────────────────

    def run(self):
        """Start the TCP server and accept connections."""
        self.config.load()
        self.config.start_csv_flush_thread()

        bind_host = self.config.host
        bind_port = self.config.port

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server.bind((bind_host, bind_port))
            server.listen(5)
            if self.config.cache_ttl > 0:
                log(f"JollyMX server listening on {bind_host}:{bind_port} (cache {self.config.cache_ttl} seconds)")
            else:
                log(f"JollyMX server listening on {bind_host}:{bind_port} (no cache)")

            background_thread = threading.Thread(target=self.jobs_thread, daemon=True)
            background_thread.start()

            while True:
                conn, addr = server.accept()

                if not self.config.is_allowed(addr[0]):
                    log(f"Rejected connection from {addr[0]} (not in allowed_hosts)", to_stderr=True)
                    log_to_file(f"Rejected connection from {addr[0]}")
                    conn.close()
                    continue

                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(conn, addr),
                    daemon=True
                )
                client_thread.start()

        except Exception as e:
            log(f"Failed to start server: {e}", to_stderr=True)
            sys.exit(1)

        finally:
            server.close()
