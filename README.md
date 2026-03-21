# Jolly MX Router Service

This service acts as a Postfix Policy Server to dynamically route emails based on both the sender and the recipient addresses. See [SMTPD Access Policy Delegation](https://www.postfix.org/SMTPD_POLICY_README.html)

It implements a Weighted Round Robin to warm up mx servers

This project started as a fork of [postfix-mx-pattern-router](https://github.com/filidorwiese/postfix-mx-pattern-router) by [Filidor Wiese](https://github.com/filidorwiese), but it is **no longer compatible**, neither in configuration, nor in functionality.

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

- install python3-venv
- create a virtual environment in `.venv` and activate it
- installport requirements
- copy `jolly-mx.yaml.example` to `/etc/postfix/jolly-mx.yaml`, edit your server groups and pattern rules
- run the service for testing

```bash
    $ sudo apt-get install python3-venv
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
        check_policy_service inet:127.0.0.1:9732,
        permit_mynetworks,
        permit_sasl_authenticated,
        reject_unauth_destination
```

Ensure that `check_policy_service` is before `permit_mynetworks` and `permit_sasl_authenticated`, else it will not be triggered for local traffic i.e. webmail.

Then reload Postfix:

```bash
$ postfix reload
```

## Configuration

Edit `/etc/postfix/jolly-mx.yaml` to your needs and reload the service with:

```bash
$ systemctl restart jolly-mx
```

Begin with `enabled: false`, then inspect the logs and only enable it once it behaves as you expect.
The log files locations are set in `/etc/postfix/jolly-mx.yaml`.

### Combined Rules

Combined rules let you fine-tune server selection based on the **combination** of sender and recipient rule results. They are evaluated **before** the individual sender/recipient fallback, so a combined rule always takes precedence.

The key format is `"sender_group,recipient_group"`, and the value can be either a group name or an explicit list of server names:

```yaml
combined_rules:
  # Use the existing "picky" group
  "good,picky": picky

  # Override: bad sender to a 'good' recipient still uses the bad servers
  "bad,good": bad

  # Explicit server list
  "bad,picky": [mx7]
  "bad,gmail": [mx5, mx6]

  # Another group name
  "bad,microsoft": microbad
```

If no combined rule matches, the service falls back to the recipient rule, then the sender rule.

See **Testing your rules** below.

### Security

#### Allowed Hosts

Restrict which servers may connect using `allowed_hosts` in the config:

```yaml
config:
  allowed_hosts: [127.0.0.1, 10.0.0.1, postfix.example.com]
```

Accepts IPv4, IPv6 addresses and DNS names (resolved at startup). Leave empty or set to `0.0.0.0` to accept from all. Rejected connections are logged to stderr and to the log file.

#### Input Sanitization

All incoming requests are validated before processing:

- **Size limit**: requests larger than 10 KB are rejected
- **Required fields**: `protocol_name`, `sender`, and `recipient` must be present
- **Email validation**: sender and recipient are checked against a standard email regex

Invalid requests are logged and responded to with `DUNNO`.

## Testing your rules

Once you have everything set up with `enabled: false` in the configuration jolly-mx will start logging and updating the csv file `/var/log/jolly-mx-messages.csv`.

Now it's time to start creating your servers, groups, sender and recipient rules and combined rules.
At first you might want to keep `verbose: true` to inspect the actual Postfix payloads.

Once you are satisfied with your configuration, run jolly-mx from the command line, you will receive an error message if there is a syntax error.

```bash
    $ python3 jolly-mx.py -c /etc/postfix/jolly-mx.yaml
    ERROR: Failed to parse YAML configuration file jolly-mx.yaml:
      mapping values are not allowed here
      in "jolly-mx.yaml", line 5, column 7

```

Once it starts, it means the syntax is ok. You can stop it with `CTRL-C` and restart the service with

```bash
    $ sudo systemctl restart jolly-mx
```

Try to work in small increments. All the while the csv file will grow. As long as you keep `enabled: false` you can collect actual traffic to test your rules on.

### Testing with collected traffic

Once you have collected enough traffic, you can test your rules with the `tests/test_rules.py` script.

```bash
    $ python3 tests/test_rules.py -c /etc/postfix/jolly-mx.yaml -i /var/log/jolly-mx-messages.csv
```

This way you can review your latest rules against your mailserver's actual traffic, inspect the decisions made and the load across servers.

Repeat until happy, then turn `enabled:true` and watch the logs for a bit to ensure everything is working as expected. Review the logs for a couple of days, then turn `verbose:false` to only log errors and statistics.

### Testing the code

The `tests/run_all.py` script will run all but the load tests and report the results. Run the individual tests to see their detailed output.

```bash
    $ python3 tests/run_all.py
    $ python3 tests/test_full.py
    ...
    # load test makes 273,000 requests on my system in less than 2 seconds
    $ python3 tests/load_test.py
  # load concurrent makes 680,000 requests on my system in less than 6 seconds
    $ python3 tests/load_concurrent.py
```

## End of jolly-mx specific part

I am attaching the mx matching description from the original README by [Filidor Wiese](https://github.com/filidorwiese) below, as it appeared at the time of my original fork October 3rd, 2025.

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

```

```
