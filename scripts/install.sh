#!/bin/bash
# Vignette Installation Script
# For Raspberry Pi Zero 2 W with Waveshare 7.3" e-paper display
#
# Usage: bash scripts/install.sh
# Estimated time: 10-20 minutes

set -e

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "============================================"
echo "  Vignette - H System Smart Display"
echo "  Install directory: $INSTALL_DIR"
echo "============================================"

# ── Step 1: System packages ──────────────────────────────────────────
echo ""
echo "[1/5] Installing system packages..."
sudo apt-get update
sudo apt-get -y upgrade
sudo apt-get -y install \
    python3-dev \
    python3-venv \
    python3-pip \
    git \
    tmux

# ── Step 2: Enable SPI ──────────────────────────────────────────────
echo ""
echo "[2/5] Enabling SPI interface..."
if command -v raspi-config &> /dev/null; then
    sudo raspi-config nonint do_spi 0
    echo "SPI enabled."
else
    echo "WARNING: raspi-config not found. Please enable SPI manually."
fi

# ── Step 3: Python virtual environment ──────────────────────────────
echo ""
echo "[3/5] Setting up Python virtual environment..."
cd "$INSTALL_DIR"
python3 -m venv venv
. venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

echo "Python packages installed."

# ── Step 4: Create output directory ─────────────────────────────────
echo ""
echo "[4/5] Creating output directory..."
cd "$INSTALL_DIR"
mkdir -p output

# ── Step 5: Install systemd service ────────────────────────────────
echo ""
echo "[5/5] Setting up systemd service for auto-start..."

# Create service file with correct paths
SERVICE_FILE="/etc/systemd/system/vignette.service"
sudo bash -c "cat > $SERVICE_FILE" << SVCEOF
[Unit]
Description=Vignette - H System Smart Display Web Interface
Documentation=https://github.com/teyrallc/photopainter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/web/app.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable vignette
sudo systemctl start vignette

echo "Service installed and started."

# Get IP address for display
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "============================================"
echo "  Installation Complete!"
echo "============================================"
echo ""
echo "  Vignette Web 介面已啟動並設為開機自動執行！"
echo ""
echo "  從任何裝置存取 / Access from any device:"
echo "    http://${PI_IP}:5000"
echo ""
echo "  服務管理 / Service management:"
echo "    sudo systemctl status vignette   # 查看狀態"
echo "    sudo systemctl restart vignette  # 重啟服務"
echo "    sudo systemctl stop vignette     # 停止服務"
echo "    journalctl -u vignette -f        # 查看日誌"
echo ""
echo "  遠端更新 / Remote update:"
echo "    bash scripts/update.sh               # SSH 更新"
echo "    或在 Web 控制台點擊「遠端更新程式」   # Web 更新"
echo ""
