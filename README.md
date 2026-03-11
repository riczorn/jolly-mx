# Jolly MX Router Service

This service acts as a Postfix Policy Server to dynamically route emails based on both the sender and the recipient addresses.

It implements a Weighted Round Robin for Postfix Policy Server [SMTPD Access Policy Delegation](https://www.postfix.org/SMTPD_POLICY_README.html)

This project started as a fork of [postfix-mx-pattern-router](https://github.com/filidorwiese/postfix-mx-pattern-router) by Filidor Wiese and uses its mx lookup logic.

## Main features

- support for Weighted Round Robin mx server groups
- gradually warm up new mailservers (using `perc`)
- each rule can target a specific group
- all servers are used if no group is chosen by a rule
- a default rule will override the full list of servers
- the configuration in yaml
  - **server perc** is the percentage out of 100 that this server should be chosen
  - **default** allows you to specify a default group; otherwise all servers are used
  - 💡 The script will look for `jolly-mx.yaml` in `/etc/postfix/` first, and then in its local directory unless overridden by `-c`.

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

### 1. With install script

There is an install script that may help you create the virtual environment, install the requirements and setup the service.

Clone this repository and run the install script:

```bash
    $ cd /opt
    $ git clone https://github.com/riczorn/jolly-mx.git
    $ cd jolly-mx
    $ ./install_service.sh
```

This should take care of installing and creating the service. Check the service status with

```bash
    $ systemctl status jolly-mx
```

### 1. Manual installation

Else, to quickly set it up, after checking out the code,

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

### 2. Testing

You can find the tests in the `tests` folder.
Query the service with

```bash
    $ cat <<EOF | nc 127.0.0.1 9732
request=smtpd_access_policy
sender=newsletter@fasterweb.net
recipient=xyz@gmail.com

EOF
```

#### Expected response

The service responds with:

- `action=FILTER smtp:[mx_address]` if a match is found
- `action=DUNNO` if **no** match is found (Postfix continues as normal)

You will also find in the configured log files the messages received and their result.

### 3. Integration with Postfix

Once you confirm that the service is working, you may configure Postfix.

Add the following to your Postfix configuration (`/etc/postfix/main.cf`) under `smtpd_recipient_restrictions`:

```
smtpd_relay_restrictions =
        check_policy_service inet:127.0.0.1:9732,
        ...
```

For example this could be:

```
smtpd_relay_restrictions =
        check_policy_service inet:127.0.0.1:10099,
        permit_mynetworks,
        permit_sasl_authenticated,
        reject_unauth_destination
```

Ensure that `check_policy_service` is before `permit_mynetworks` and `permit_sasl_authenticated`, else it will not be triggered for local traffic i.e. webmail.

Then reload Postfix:

```bash
$ postfix reload
```

### 4. Configuration

Edit `/etc/postfix/jolly-mx.yaml` to your needs and reload the service with:

```bash
$ systemctl restart jolly-mx
```

Begin with `enabled: false`, then inspect the logs and only enable it once it behaves as you expect.
The log files locations are set in `/etc/postfix/jolly-mx.yaml`.

## End of jolly-mx specific part

I am attaching the mx matching description from the original README below, as it appeared at the time of this fork October 3rd, 2025.

# Postfix MX Pattern Router Service

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

## License

This project is licensed under the BSD 3-Clause License - see the LICENSE file for details.

https://github.com/riczorn/jolly-mx
