#!/usr/bin/env python3
"""
JollyMX - Postfix MX Pattern Router Service + Round-Robin

This script implements a TCP server that integrates with Postfix as
a policy service for dynamic routing of emails based on both the 
sender and the recipient addresses.

Usage:
    python3 jolly-mx.py [options]

Options:
    -c, --config FILE    Path to configuration file (default: /etc/postfix/postfix-mx-pattern-router.conf)
    -p, --port PORT      Port to listen on (default: 10099)
    -H, --host HOST      Host to bind to (default: 127.0.0.1)
    --cache-ttl SEC      Cache TTL in seconds (default: 3600, where 0 disables cache)
    --timeout SEC        Client inactivity timeout in seconds (default: 30, where 0 disables timeout)
    -v, --verbose        Increase verbosity level of logging

Configuration File Format:
    The jolly-mx-yaml.example contains comments explaining the format.

Description:
    Postfix sends the sender and recipient mail addresses to this policy service, which
    determines the transport to use for each email based on rules defined in the configuration file.

    This script expects input from Postfix with key-value pairs separated by '=', one per line, and empty
    lines as request separators (Postfix policy delegation protocol).

    Example input:
    request=smtpd_access_policy
    protocol_state=RCPT
    protocol_name=SMTP
    sender=sender@example.com
    recipient=recipient@example.com

    Example output:
    action=FILTER relay:[mailtransport.example.com]:25

    The script responds with a Postfix-compatible action based on the
    most specific matching pattern.

Postfix Configuration:
    1. In main.cf, add the following under smtpd_recipient_restrictions:
    smtpd_recipient_restrictions =
        ... existing restrictions ...
        check_policy_service { inet:127.0.0.1:9732, timeout=10s, default_action=DUNNO }

    2. for higher throughput, the Postfix docs recommend spawning a separate process:
        spawn_command = /path/to/jolly-mx.py. I don't think this makes sense here I get 
        over 5,000 responses per second it shouldn't need it.

    Then reload Postfix:
        systemctl reload postfix

Useful links:
 - https://www.postfix.org/transport.5.html
 - https://www.postfix.org/tcp_table.5.html
 - https://github.com/fbett/postfix-tcp-table-service
"""

import os
import time
import dns.resolver

# Change to the script's directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import src.config as cfg
from src.service import PolicyService

config = cfg.Config()


# ── DNS / MX Cache ───────────────────────────────────────────────────

def get_mx_records(domain, cache_ttl):
    """Get MX records for a domain using dns.resolver with optional caching.

    Returns:
        tuple: (mx_records, from_cache) where:
            - mx_records is a list of MX hostnames
            - from_cache is a boolean indicating if the result came from cache
    """
    current_time = time.time()

    # Check if caching is enabled (positive TTL) and we have a valid cached entry
    with service.cache_lock:
        if cache_ttl > 0 and domain in service.mx_cache:
            cache_time, mx_records = service.mx_cache[domain]
            if current_time - cache_time < cache_ttl:
                return mx_records, True

    # No valid cache entry or caching disabled, perform DNS lookup
    try:
        answers = dns.resolver.resolve(domain, 'MX')
        mx_records = [answer.exchange.to_text().rstrip('.').lower() for answer in answers]

        # Cache the result if caching is enabled
        if cache_ttl > 0:
            with service.cache_lock:
                service.mx_cache[domain] = (current_time, mx_records)

        return mx_records, False
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        # Cache empty result if caching is enabled
        if cache_ttl > 0:
            with service.cache_lock:
                service.mx_cache[domain] = (current_time, [])

        return [], False


# ── Business Logic ───────────────────────────────────────────────────

def get_mx_for_message(sender, recipient, cache_ttl):
    sender_result = "n/a"
    recipient_result = "n/a"

    # 1. Resolve sender group
    if sender:
        sender_result, _ = get_rule_match_for_email(sender, cache_ttl, rule_type="sender_rules")
    
    # 2. Resolve recipient group
    if recipient:
        recipient_result, _ = get_rule_match_for_email(recipient, cache_ttl, rule_type="recipient_rules")

    # 3. Check combined rules
    combined_key = f"{sender_result},{recipient_result}"
    if hasattr(config, 'combined_rule_groups') and hasattr(config.combined_rule_groups, combined_key):
        servers_obj = getattr(config.combined_rule_groups, combined_key)
        mx = servers_obj.get_next().address
        return mx, f"combined:{combined_key}"

    # 4. Fallback: recipient rules
    if recipient_result and recipient_result != "n/a":
        mx, group = pick_server_for_group(recipient_result)
        if mx and mx != "NO RESULT":
            return mx, group

    # 5. Fallback: sender rules
    if sender_result and sender_result != "n/a":
        mx, group = pick_server_for_group(sender_result)
        if mx and mx != "NO RESULT":
            return mx, group

    # 6. Global fallback if roundrobin is enabled
    if config.roundrobin:
        mx_server = config.servers_obj.get_next()
        if mx_server:
            return mx_server.address, "roundrobin"

    return False, "n/a"


def get_rule_match_for_email(email, cache_ttl, rule_type):
    """
    Find the matching rule for an email without picking a server.
    """
    mx_server_group = False
    default = False
    domain = email.split('@')[1] if '@' in email else ''

    if domain:
        # Skip DNS lookups for sender rules — they match on email/domain, not MX records
        if rule_type != "sender_rules":
            mx_records, _ = get_mx_records(domain, cache_ttl)
            for mx in mx_records:
                mx_server_group, default = config.test_domain_rules(email, mx, rule_type=rule_type)
                if mx_server_group:
                    break
        
        # Try matching directly on the email/domain
        if not mx_server_group:
            mx_server_group, default = config.test_domain_rules(email, domain, rule_type=rule_type)

    if not mx_server_group:
        mx_server_group = default if default else "n/a"

    return mx_server_group, default


def pick_server_for_group(mx_server_group):
    """
    Pick a server based on the matched group name.
    """
    if mx_server_group == 'NO RESULT':
        return "NO RESULT", mx_server_group

    if not mx_server_group or mx_server_group == "n/a":
        return False, False
        
    servers_obj = config.get_server_group(mx_server_group)
    if not servers_obj:
        return False, mx_server_group

    mx = servers_obj.get_next(mx_server_group).address

    return mx, mx_server_group


def get_next_server_for_email(email, cache_ttl, rule_type):
    mx_server_group, _ = get_rule_match_for_email(email, cache_ttl, rule_type)
    return pick_server_for_group(mx_server_group)
    

# ── Entry Point ──────────────────────────────────────────────────────

# Create the service instance at module-level so that business logic functions
# (get_mx_records, etc.) can access mx_cache/cache_lock even when imported
# without calling main() (e.g. from tests).
service = PolicyService(config, get_mx_for_message)


def main():
    cfg.config = config
    service.register_signals()
    service.run()


if __name__ == "__main__":
    main()
