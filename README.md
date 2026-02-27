# Jolly MX Router Service

Implement a Weighted Round Robin for Postfix Policy Server (SMTPD Policy Delegation).

This project started as a fork of [postfix-mx-pattern-router](https://github.com/filidorwiese/postfix-mx-pattern-router) but is incompatible with the original configuration.

This fork makes substantial changes to the original project by Filidor Wiese:

- support for Weighted Round Robin mx server groups
- each rule can target a specific group
- all servers are used if no group is chosen by a rule and no default rule is set
- server groups have the same percentage usage as the main list. 
  keep this into consideration when choosing the percentage for the individual servers
- New configuration in yaml
    - **server perc** is the percentage out of 100 that this server should be chosen
    - **default** allows you to specify a default group; otherwise all servers are used
    - 💡 The script will look for `jolly-mx.yaml` in `/etc/postfix/` first, and then in its local directory unless overridden by `-c`.
    - copy `jolly-mx.yaml.example` to `/etc/postfix/jolly-mx.yaml`, edit your server groups and pattern rules

- on CTRL-C exit gracefully and show some stats such as : 
```
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
```

## Installation
To quickly set it up, after checking out the code, 
- create a virtual environment in `.venv` and activate it
- installport requirements
- copy `jolly-mx.yaml.example` to `/etc/postfix/jolly-mx.yaml`, edit your server groups and pattern rules
- run the service for testing

```bash
    $ python -m venv .venv
    $ . .venv/bin/activate
    $ pip install -r requirements.txt
    $ python jolly-mx.py -v
```

- query the service with

```bash
    $ cat <<EOF | nc 127.0.0.1 10099
request=smtpd_access_policy
sender=newsletter@fasterweb.net
recipient=xyz@gmail.com

EOF
```

## Expected response
The service responds with:
- `action=FILTER smtp:[mx_address]` if a match is found
- `action=DUNNO` if **no** match is found (Postfix continues as normal)

## End of jolly-mx specific part

Please find the original README below, as it appeared at the time of this fork October 3rd, 2025; most of it is still valid, 
The only notable difference is the different name: `jolly-mx.py` and **different configuration** filename (`jolly-mx.yaml`), format and options. Also, it operates as a **Postfix Policy Server** rather than a tcp lookup table.


# Postfix MX Pattern Router Service

This service acts as a Postfix Policy Server to dynamically route emails based on both the sender and the recipient addresses.

## Operation

When Postfix needs to deliver an email, it queries this service with the destination domain. The service:

1. Looks up the domain's MX records
2. Compares them against the defined patterns in the configuration file
3. If a match is found, it returns the corresponding relay server
4. If no match is found, Postfix will use its default transport (usually direct delivery)

This can be useful to, for example, optimize email delivery for domains that use the Microsoft mail infrastructure by routing these emails through specialized third-party SMTP relays with established sender reputations.

### Pattern Matching Behavior

The service uses substring matching for MX patterns, not exact matching. This means:

- Patterns like `protection.outlook.com` will match MX records such as `hotmail-com.olc.protection.outlook.com`
- You can use shorter, more generic patterns to match multiple similar MX records
- The first pattern that matches any part of an MX record will be used
- Patterns are checked in the order they appear in the configuration file

**Please be aware that patterns are not matched against recipient domain but the MX records of that domain!**

## Installation

### Requirements

- Python 3.6 or higher

### Setup

1. Clone this repository:

```bash
$ git clone https://github.com/filidorwiese/jolly-mx.git /usr/local/bin/jolly-mx
$ cd /usr/local/bin/jolly-mx
```

2. Install dependencies:

```bash
$ pip install -r requirements.txt
```

Or use package manager from your distribution.

3. Create the configuration file to define your MX patterns:

```bash
$ nano /etc/postfix/jolly-mx.yaml
```

Example configuration:
```
protection.outlook.com    relay:[office365-relay.example.com]:587
mx.microsoft              relay:[office365-relay.example.com]:587
icloud.com                relay:[icloud-relay.example.com]:587
```

## Running as a Service

An automated configuration script `install_service.sh` is provided. It handles:
- Creating a dedicated `jolly-mx` system user/group with no login access
- Setting up the Python virtual environment and dependencies locally
- Copying the configuration to `/etc/postfix/jolly-mx.yaml`
- Generating and starting the systemd unit `jolly-mx.service` dynamically based on your current path

To install:
```bash
$ sudo ./install_service.sh
```

Check the status at any time:
```bash
$ systemctl status jolly-mx
```

## Integration with Postfix

Add the following to your Postfix configuration (`/etc/postfix/main.cf`):

Add the following to your Postfix configuration (`/etc/postfix/main.cf`) under `smtpd_recipient_restrictions`:

```
smtpd_recipient_restrictions =
    ...,
    check_policy_service inet:127.0.0.1:10099
```

Then reload Postfix.

## Testing the Service

You can test the service directly from the command line using netcat (nc) to simulate Postfix policy delegation requests:

```bash
$ cat <<EOF | nc 127.0.0.1 10099
request=smtpd_access_policy
sender=newsletter@fasterweb.net
recipient=user@tiscali.it

EOF
```

The service responds with:
- `action=FILTER smtp:[...relay...]` if a match is found
- `action=DUNNO` if no match is found

You can also check the logs for more detailed information:

```bash
$ journalctl -u jolly-mx -f
```

## License
This project is licensed under the BSD 3-Clause License - see the LICENSE file for details.

https://github.com/riczorn/jolly-mx
