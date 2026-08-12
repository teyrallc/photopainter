#!/bin/bash
# Vignette Installation Script
# For Raspberry Pi Zero 2 W with Waveshare 7.3" e-paper display
#
# Usage: bash scripts/install.sh
# Estimated time: 10-20 minutes

set -e

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "============================================"
echo "  Vignette Smart Display"
echo "  Install directory: $INSTALL_DIR"
echo "============================================"

# ── Step 1: System packages ──────────────────────────────────────────
echo ""
echo "[1/7] Installing system packages..."
sudo apt-get update
sudo apt-get -y upgrade
sudo apt-get -y install \
    python3-dev \
    python3-venv \
    python3-pip \
    git \
    tmux \
    dnsmasq \
    iptables

# dnsmasq is needed for NetworkManager shared mode DNS.
# Disable the system service so it doesn't conflict with NM's managed instance.
echo "Disabling system dnsmasq service (NM will manage its own)..."
sudo systemctl stop dnsmasq 2>/dev/null || true
sudo systemctl disable dnsmasq 2>/dev/null || true
sudo systemctl mask dnsmasq 2>/dev/null || true

# ── Step 2: Enable SPI ──────────────────────────────────────────────
echo ""
echo "[2/7] Enabling SPI interface..."
if command -v raspi-config &> /dev/null; then
    sudo raspi-config nonint do_spi 0
    echo "SPI enabled."
else
    echo "WARNING: raspi-config not found. Please enable SPI manually."
fi

# ── Step 3: Python virtual environment ──────────────────────────────
echo ""
echo "[3/7] Setting up Python virtual environment..."
cd "$INSTALL_DIR"
python3 -m venv venv
. venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

echo "Python packages installed."

# ── Step 4: Create output directory ─────────────────────────────────
echo ""
echo "[4/7] Creating output directory..."
cd "$INSTALL_DIR"
mkdir -p output

# ── Step 5: Service account ─────────────────────────────────────────
# The service used to run as root. It is reachable from the public internet
# through the remote-access tunnel, so it gets its own unprivileged account
# and a narrow sudo allowlist instead of the whole machine.
echo ""
echo "[5/7] Creating the service account..."

SERVICE_USER="vignette"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    sudo useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "Created user '$SERVICE_USER'."
else
    echo "User '$SERVICE_USER' already exists."
fi

# Panel over SPI, GPIO for the busy/reset pins, netdev so NetworkManager
# accepts commands from it. Missing groups are skipped rather than fatal —
# they vary between Raspberry Pi OS releases.
for grp in spi gpio netdev; do
    if getent group "$grp" >/dev/null 2>&1; then
        sudo usermod -aG "$grp" "$SERVICE_USER"
    else
        echo "NOTE: group '$grp' does not exist on this system; skipped."
    fi
done

# The service writes photos into output/, its config beside them, and
# scripts/update.sh runs git in the working tree — all of which need the
# checkout to belong to it.
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

# Exactly the privileged operations the interface offers, and nothing else.
SUDOERS_FILE="/etc/sudoers.d/vignette"
sudo bash -c "cat > $SUDOERS_FILE" << 'SUDOEOF'
# Vignette service account: the privileged actions the web interface exposes.
# Deliberately specific — no blanket NOPASSWD: ALL.
vignette ALL=(root) NOPASSWD: /usr/bin/nmcli, /bin/nmcli
vignette ALL=(root) NOPASSWD: /sbin/reboot, /usr/sbin/reboot
vignette ALL=(root) NOPASSWD: /sbin/shutdown, /usr/sbin/shutdown
vignette ALL=(root) NOPASSWD: /bin/systemctl restart vignette
vignette ALL=(root) NOPASSWD: /usr/bin/systemctl restart vignette
vignette ALL=(root) NOPASSWD: /usr/bin/systemd-run --collect --quiet --unit=vignette-restart-* --description=* /bin/sh -c *
SUDOEOF
sudo chmod 0440 "$SUDOERS_FILE"

# A malformed sudoers file locks everyone out of sudo, so validate and roll
# back rather than leaving a broken one in place.
if sudo visudo -c -f "$SUDOERS_FILE" >/dev/null 2>&1; then
    echo "Sudo allowlist installed."
else
    echo "ERROR: the sudoers file failed validation and has been removed."
    sudo rm -f "$SUDOERS_FILE"
    exit 1
fi

# ── Step 6: Install systemd service ────────────────────────────────
echo ""
echo "[6/7] Setting up systemd service for auto-start..."

SERVICE_FILE="/etc/systemd/system/vignette.service"
sudo bash -c "cat > $SERVICE_FILE" << SVCEOF
[Unit]
Description=Vignette Smart Display
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
SupplementaryGroups=spi gpio netdev
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/web/app.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
PrivateTmp=yes
ProtectSystem=full
ProtectHome=no
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable vignette
sudo systemctl restart vignette

echo "Service installed and started."

echo ""
echo "[7/7] Checking that the service stayed up..."
sleep 5
if sudo systemctl is-active --quiet vignette; then
    echo "Service is running."
else
    echo "WARNING: the service is not running. Check the log with:"
    echo "    journalctl -u vignette -n 50 --no-pager"
fi

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
