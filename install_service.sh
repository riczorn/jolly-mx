#!/bin/bash

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

echo "[*] Setting up virtual environment..."
# Run as jolly-mx so it owns the venv files
sudo -u jolly-mx python3 -m venv "$DIR/.venv"
sudo -u jolly-mx "$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

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
ExecStart=$DIR/.venv/bin/python $DIR/jolly-mx.py -p 10099 --cache-ttl 3600
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
echo "Integration with Postfix:"
echo "Add 'check_policy_service inet:127.0.0.1:10099' to smtpd_recipient_restrictions in /etc/postfix/main.cf"
echo "=============================================="
