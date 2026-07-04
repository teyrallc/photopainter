#!/bin/bash
# Vignette Installation Script
# For Raspberry Pi Zero 2 W with Waveshare 7.3" e-paper display
#
# Usage: bash scripts/install.sh
# Estimated time: 10-20 minutes

set -e

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Run the service as the invoking (non-root) user, not root.
RUN_USER="${SUDO_USER:-$USER}"

echo "============================================"
echo "  Vignette - Smart Display"
echo "  Install directory: $INSTALL_DIR"
echo "  Service user:      $RUN_USER"
echo "============================================"

# ── Step 1: System packages ──────────────────────────────────────────
echo ""
echo "[1/7] Installing system packages..."
sudo apt-get update
# NOTE: no blanket `apt-get upgrade` — it is slow on a Pi Zero, may pull kernel
# updates needing a reboot mid-install, and can abort the whole script under set -e.
sudo apt-get -y install \
    python3-dev \
    python3-venv \
    python3-pip \
    git \
    tmux \
    network-manager \
    dnsmasq \
    iptables

# Every networking feature (AP hotspot, scan, connect) shells out to nmcli, so
# NetworkManager MUST be present and active. Fail fast if it isn't.
echo "Enabling NetworkManager..."
sudo systemctl enable --now NetworkManager || true
# dhcpcd (Bullseye/Lite images) conflicts with NM managing wlan0.
if systemctl list-unit-files | grep -q '^dhcpcd\.service'; then
    echo "Disabling dhcpcd (conflicts with NetworkManager)..."
    sudo systemctl disable --now dhcpcd 2>/dev/null || true
fi
if ! command -v nmcli >/dev/null 2>&1; then
    echo "FATAL: nmcli not found after install — NetworkManager is required." >&2
    exit 1
fi

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

# ── Step 4: Output dir + ownership + hardware groups ────────────────
echo ""
echo "[4/7] Creating output directory and setting permissions..."
mkdir -p output
# The service user owns the project so it can write config.json/output and run
# git updates without root-owned files, and joins the hardware access groups.
sudo chown -R "$RUN_USER":"$RUN_USER" "$INSTALL_DIR"
sudo usermod -aG spi,gpio,netdev "$RUN_USER" 2>/dev/null || true

# ── Step 5: Scoped privileges (no full root) ────────────────────────
echo ""
echo "[5/7] Granting scoped privileges (polkit + sudoers)..."

# Let the service user control NetworkManager (AP/wifi) without sudo/root.
POLKIT_RULE="/etc/polkit-1/rules.d/50-vignette-nm.rules"
sudo bash -c "cat > $POLKIT_RULE" << POLKITEOF
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 &&
        subject.user == "$RUN_USER") {
        return polkit.Result.YES;
    }
});
POLKITEOF

# Allow ONLY reboot/shutdown/service-restart via sudo without a password.
SUDOERS_FILE="/etc/sudoers.d/vignette"
sudo bash -c "cat > $SUDOERS_FILE" << SUDOEOF
$RUN_USER ALL=(root) NOPASSWD: /sbin/reboot, /usr/sbin/reboot, /sbin/shutdown, /usr/sbin/shutdown, /bin/systemctl restart vignette, /usr/bin/systemctl restart vignette, /bin/systemctl restart --no-block vignette, /usr/bin/systemctl restart --no-block vignette
SUDOEOF
sudo chmod 0440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" >/dev/null

# ── Step 6: Install systemd service (rendered from template) ────────
echo ""
echo "[6/7] Setting up systemd service for auto-start..."
SERVICE_FILE="/etc/systemd/system/vignette.service"
sed -e "s#__INSTALL_DIR__#$INSTALL_DIR#g" \
    -e "s#__RUN_USER__#$RUN_USER#g" \
    "$INSTALL_DIR/scripts/vignette.service" | sudo tee "$SERVICE_FILE" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable vignette
sudo systemctl restart vignette

echo "Service installed and started."

# ── Step 7: Done ────────────────────────────────────────────────────
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
echo "  NOTE: group membership (spi/gpio/netdev) takes effect on next login;"
echo "  if hardware access fails on first run, reboot once."
echo ""
echo "  遠端更新 / Remote update:"
echo "    bash scripts/update.sh               # SSH 更新"
echo "    或在 Web 控制台點擊「遠端更新程式」   # Web 更新"
echo ""
