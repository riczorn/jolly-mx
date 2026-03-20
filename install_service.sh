#!/bin/bash

LOGFILE="/var/log/jolly-mx.log"
CSVFILE="/var/log/jolly-mx-messages.csv"

# Ensure running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script as root (e.g. using sudo)."
  exit 1
fi

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "=============================================="
echo " Jolly MX Service Installer"
echo "=============================================="
echo "This script will:"
echo " 1. Create a dedicated system user 'jolly-mx'"
echo " 2. Create a virtual environment and install dependencies"
echo " 3. Copy jolly-mx.yaml.example to /etc/postfix/jolly-mx.yaml (if missing)"
echo " 4. Create and enable systemd service 'jolly-mx.service'"
echo "    running from: $DIR"
echo ""
read -p "Do you want to proceed? [y/N]: " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Installation aborted."
    exit 0
fi

echo "[*] Creating system user 'jolly-mx'..."
if id "jolly-mx" &>/dev/null; then
    echo "User jolly-mx already exists."
else
    # Works on Debian/Ubuntu and RedHat/CentOS
    groupadd --system jolly-mx || true
    useradd --system --no-create-home --shell /usr/sbin/nologin -g jolly-mx jolly-mx || true
fi

echo "[*] Modifying ownership for jolly-mx..."
chown -R jolly-mx:jolly-mx "$DIR"

echo "[*] Creating the log file..."
touch "$LOGFILE"
chown jolly-mx:jolly-mx "$LOGFILE"
chmod 666 "$LOGFILE"
touch "$CSVFILE"
chown jolly-mx:jolly-mx "$CSVFILE"
chmod 666 "$CSVFILE"

echo "[*] Checking for python3 and compile dependencies..."
if ! command -v python3 &>/dev/null || ! command -v gcc &>/dev/null || ! ls /usr/include/python3*/Python.h &>/dev/null; then
    echo "python3, gcc or python3 headers are missing. Attempting to install..."
    if command -v apt-get &>/dev/null; then
        apt-get update && \
        DEBIAN_FRONTEND=noninteractive apt-get install -y python3 gcc && \
        DEBIAN_FRONTEND=noninteractive apt-get install -y python3-dev
    elif command -v dnf &>/dev/null; then
        dnf install -y python3 python3-devel gcc
    elif command -v yum &>/dev/null; then
        yum install -y python3 python3-devel gcc
    elif command -v zypper &>/dev/null; then
        zypper install -y python3 python3-devel gcc
    elif command -v pacman &>/dev/null; then
        pacman -Sy --noconfirm python gcc
    else
        echo "=========================================================================="
        echo "⚠️  Could not find a supported package manager to install dependencies."
        echo "Please install python3, python3-dev and gcc manually."
        echo "=========================================================================="
        read -p "Press Enter to continue once you have installed them, or Ctrl-C to abort."
    fi
fi

echo "[*] Checking for python3 venv support..."
if ! python3 -m venv --help &>/dev/null; then
    echo "python3-venv is not installed. Attempting to install..."
    if command -v apt-get &>/dev/null; then
        apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv
    elif command -v dnf &>/dev/null; then
        dnf install -y python3-venv
    elif command -v yum &>/dev/null; then
        yum install -y python3-venv
    elif command -v zypper &>/dev/null; then
        zypper install -y python3-venv
    elif command -v pacman &>/dev/null; then
        pacman -Sy --noconfirm python
    else
        echo "=========================================================================="
        echo "⚠️  Could not find a supported package manager to install python3-venv."
        echo "Please install it manually."
        echo "=========================================================================="
        read -p "Press Enter to continue once you have installed it, or Ctrl-C to abort."
    fi
fi

echo "[*] Setting up virtual environment..."
# Run as jolly-mx so it owns the venv files
sudo -u jolly-mx python3 -m venv "$DIR/.venv"
sudo -u jolly-mx "$DIR/.venv/bin/pip" install --no-cache-dir -r "$DIR/requirements.txt"

echo "[*] Setting up configuration..."
if [ ! -d "/etc/postfix" ]; then
    mkdir -p /etc/postfix
fi

if [ ! -f "/etc/postfix/jolly-mx.yaml" ]; then
    cp "$DIR/jolly-mx.yaml.example" "/etc/postfix/jolly-mx.yaml"
    echo "Created /etc/postfix/jolly-mx.yaml from example."
else
    echo "/etc/postfix/jolly-mx.yaml already exists, leaving it untouched."
fi

echo "[*] Creating systemd service file..."
cat <<EOF > /etc/systemd/system/jolly-mx.service
[Unit]
Description=Jolly MX Policy Server
After=network.target

[Service]
ExecStart=$DIR/.venv/bin/python $DIR/jolly-mx.py
WorkingDirectory=$DIR
Restart=on-failure
User=jolly-mx
Group=jolly-mx
StandardOutput=journal
StandardError=journal
SyslogIdentifier=jolly-mx
SyslogFacility=mail

[Install]
WantedBy=multi-user.target
EOF

echo "[*] Reloading systemd daemon..."
systemctl daemon-reload

echo "[*] Enabling and starting jolly-mx service..."
systemctl enable jolly-mx
systemctl start jolly-mx
sleep 1

echo "[*] Status:"
systemctl is-active jolly-mx

echo "=============================================="
echo "Installation complete!"
echo "Check logs with: journalctl -u jolly-mx -f"
echo "----------------------------------------------"
echo "Integration with Postfix:"
echo "in /etc/postfix/main.cf add:"
echo "smtpd_recipient_restrictions ="
echo "  check_policy_service inet:127.0.0.1:9732"
echo "=============================================="
