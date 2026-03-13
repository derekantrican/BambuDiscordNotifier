#!/usr/bin/env bash
set -euo pipefail

# BambuDiscordNotifier setup script for Raspberry Pi

INSTALL_DIR="/home/pi/BambuDiscordNotifier"
SERVICE_NAME="bambu-discord-notifier"

echo "============================================"
echo "  BambuDiscordNotifier - Setup"
echo "============================================"
echo ""

# Check if running as root
if [ "$(id -u)" -eq 0 ]; then
    echo "Please run this script as a normal user (not root)."
    echo "The script will use sudo when needed."
    exit 1
fi

# Install system dependencies
echo "📦 Installing system dependencies ..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip libcamera-apps-lite

# Create project directory if not in the right place
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    echo "📁 Copying project to $INSTALL_DIR ..."
    mkdir -p "$INSTALL_DIR"
    cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/"
    cd "$INSTALL_DIR"
else
    cd "$SCRIPT_DIR"
fi

# Create Python virtual environment
echo "🐍 Creating Python virtual environment ..."
python3 -m venv venv
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo ""
echo "============================================"
echo "  Configuration"
echo "============================================"
echo ""

# Gather configuration from user
if [ ! -f config.yaml ]; then
    read -rp "Printer name (display name): " PRINTER_NAME
    PRINTER_NAME=${PRINTER_NAME:-"My Bambu Printer"}

    read -rp "Printer IP address: " PRINTER_IP
    if [ -z "$PRINTER_IP" ]; then
        echo "Error: Printer IP is required."
        exit 1
    fi

    read -rp "Printer access code: " PRINTER_ACCESS_CODE
    if [ -z "$PRINTER_ACCESS_CODE" ]; then
        echo "Error: Access code is required."
        exit 1
    fi

    read -rp "Printer serial number: " PRINTER_SN
    if [ -z "$PRINTER_SN" ]; then
        echo "Error: Serial number is required."
        exit 1
    fi

    read -rp "Discord webhook URL: " DISCORD_WEBHOOK
    if [ -z "$DISCORD_WEBHOOK" ]; then
        echo "Error: Discord webhook URL is required."
        exit 1
    fi

    read -rp "Enable Pi Camera? (y/n, default: y): " ENABLE_CAMERA
    ENABLE_CAMERA=${ENABLE_CAMERA:-y}
    if [[ "$ENABLE_CAMERA" =~ ^[Yy] ]]; then
        CAM_ENABLED="true"
    else
        CAM_ENABLED="false"
    fi

    cat > config.yaml <<EOF
printer:
  name: "${PRINTER_NAME}"
  ip: "${PRINTER_IP}"
  access_code: "${PRINTER_ACCESS_CODE}"
  serial_number: "${PRINTER_SN}"
  port: 8883

discord:
  webhook_url: "${DISCORD_WEBHOOK}"
  mention_role_id: null
  events:
    started: true
    progress: true
    progress_interval: 25
    done: true
    failed: true
    paused: true
    resumed: true
    error: true

camera:
  enabled: ${CAM_ENABLED}
  method: "libcamera"
  resolution: [1280, 720]
  include_on_events:
    - done
    - failed
    - progress

logging:
  level: "INFO"
  file: null
EOF
    echo "✅ config.yaml created."
else
    echo "config.yaml already exists — skipping configuration."
fi

# Install systemd service
echo ""
echo "🔧 Installing systemd service ..."
sudo cp service/bambu-discord-notifier.service /etc/systemd/system/${SERVICE_NAME}.service

# Update paths in service file if install dir differs
sudo sed -i "s|/home/pi/BambuDiscordNotifier|${INSTALL_DIR}|g" /etc/systemd/system/${SERVICE_NAME}.service
sudo sed -i "s|User=pi|User=$(whoami)|g" /etc/systemd/system/${SERVICE_NAME}.service

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl start ${SERVICE_NAME}

echo ""
echo "============================================"
echo "  ✅ Setup Complete!"
echo "============================================"
echo ""
echo "The service is now running. Useful commands:"
echo "  sudo systemctl status ${SERVICE_NAME}    # Check status"
echo "  sudo journalctl -u ${SERVICE_NAME} -f    # View logs"
echo "  sudo systemctl restart ${SERVICE_NAME}   # Restart"
echo "  sudo systemctl stop ${SERVICE_NAME}      # Stop"
echo ""
echo "Edit config:  nano ${INSTALL_DIR}/config.yaml"
echo "Then restart:  sudo systemctl restart ${SERVICE_NAME}"
echo ""
