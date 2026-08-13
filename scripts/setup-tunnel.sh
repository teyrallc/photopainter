#!/bin/bash
# Vignette — point your own domain at this display, over a Cloudflare Tunnel.
#
# Replaces the free ngrok address (which changes on every reconnect) with a
# fixed hostname of your own, e.g. https://yilin.example.com.
#
# Usage:
#   bash scripts/setup-tunnel.sh yilin.example.com
#
# What it does:
#   1. Installs cloudflared (official Cloudflare apt repo, arm64/armhf aware)
#   2. Logs you in to Cloudflare and lets you pick the zone
#   3. Creates a named tunnel and a DNS record for your hostname
#   4. Writes the tunnel config so it forwards to this device on port 5000
#   5. Installs cloudflared as a systemd service so it starts at boot
#   6. Points Vignette's own settings at the new address
#
# Requirements:
#   * The domain's DNS must be managed by Cloudflare (a free plan is enough)
#   * A browser, to complete the Cloudflare login — the script prints a URL
#
# No router configuration, no port forwarding, no static IP, no certificate
# management. The tunnel dials out, so nothing is exposed inbound.

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_JSON="$INSTALL_DIR/config.json"

TUNNEL_NAME="vignette"
CF_DIR="/etc/cloudflared"
CF_CONFIG="$CF_DIR/config.yml"
LOCAL_URL="http://localhost:5000"

HOSTNAME_ARG="${1:-}"

usage() {
    awk 'NR == 1 { next }
         /^#/    { sub(/^# ?/, ""); print; next }
         { exit }' "$0"
    exit "${1:-0}"
}

case "$HOSTNAME_ARG" in
    ""|-h|--help) usage 0 ;;
esac

# A bare hostname, not a URL — cloudflared wants the former.
HOSTNAME_ARG="${HOSTNAME_ARG#http://}"
HOSTNAME_ARG="${HOSTNAME_ARG#https://}"
HOSTNAME_ARG="${HOSTNAME_ARG%%/*}"

if ! printf '%s' "$HOSTNAME_ARG" | grep -qE '^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$'; then
    echo "ERROR: '$HOSTNAME_ARG' does not look like a hostname."
    echo "       Expected something like: yilin.example.com"
    exit 1
fi

echo "============================================"
echo "  Vignette — Cloudflare Tunnel setup"
echo "  Hostname: $HOSTNAME_ARG"
echo "  Forwards to: $LOCAL_URL"
echo "============================================"

# ── Step 1: install cloudflared ───────────────────────────────────────
echo ""
echo "[1/6] Installing cloudflared..."

install_from_apt() {
    sudo mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL --max-time 60 https://pkg.cloudflare.com/cloudflare-main.gpg \
        | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null || return 1

    # Cloudflare's repo lags new Debian releases, so a codename it has never
    # heard of has to fall back rather than fail. bookworm packages run fine
    # on trixie — it is a static Go binary.
    CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")"
    case "$CODENAME" in
        bullseye|bookworm|buster|focal|jammy|noble) ;;
        *) echo "  ($CODENAME is not published by Cloudflare; using bookworm)"
           CODENAME=bookworm ;;
    esac

    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared ${CODENAME} main" \
        | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null

    # Only refresh this one list. A full `apt-get update` on a flaky link can
    # take many minutes and fail on unrelated repositories.
    sudo apt-get update \
        -o Dir::Etc::sourcelist=sources.list.d/cloudflared.list \
        -o Dir::Etc::sourceparts=- \
        -o APT::Get::List-Cleanup=0 || return 1
    sudo apt-get -y install cloudflared || return 1
}

install_from_deb() {
    # Direct download, skipping the apt index entirely.
    local arch deb url
    arch="$(dpkg --print-architecture)"
    case "$arch" in
        arm64|armhf|amd64) ;;
        *) echo "  Unsupported architecture: $arch"; return 1 ;;
    esac
    deb="/tmp/cloudflared-${arch}.deb"
    url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
    echo "  Trying a direct download for $arch..."
    curl -fL --max-time 300 -o "$deb" "$url" || return 1
    sudo dpkg -i "$deb" || return 1
    rm -f "$deb"
}

if command -v cloudflared >/dev/null 2>&1; then
    echo "Already installed: $(cloudflared --version 2>&1 | head -1)"
elif install_from_apt || install_from_deb; then
    echo "Installed: $(cloudflared --version 2>&1 | head -1)"
else
    ARCH="$(dpkg --print-architecture)"
    cat <<MANUALEOF

ERROR: could not install cloudflared over the network.

  On a link too slow or lossy for apt, fetch the package on another machine
  and copy it across on a USB stick:

    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb

  Then, on this device:

    sudo dpkg -i /path/to/cloudflared-linux-${ARCH}.deb
    bash scripts/setup-tunnel.sh ${HOSTNAME_ARG}

  This script skips the install step once cloudflared is on PATH.

  Worth knowing first: a tunnel needs a steady outbound connection. If this
  device's link is dropping large packets, the tunnel will be just as
  unreliable as whatever it replaces. Check the MTU before blaming the tunnel:

    ping -M do -s 1472 -c 2 1.1.1.1     # fails => MTU below 1500
    ping -M do -s 1400 -c 2 1.1.1.1     # works => try: sudo ip link set wlan0 mtu 1400

MANUALEOF
    exit 1
fi

# ── Step 2: authenticate ──────────────────────────────────────────────
echo ""
echo "[2/6] Authenticating with Cloudflare..."

# cert.pem is the account credential; the tunnel credential is separate.
if sudo test -f /root/.cloudflared/cert.pem; then
    echo "Already authenticated."
else
    echo ""
    echo "  A URL will be printed below. Open it in ANY browser (it does not"
    echo "  have to be on this device), sign in, and pick the zone for"
    echo "  ${HOSTNAME_ARG#*.} — the one that owns this hostname."
    echo ""
    sudo cloudflared tunnel login
fi

# ── Step 3: create the tunnel ─────────────────────────────────────────
echo ""
echo "[3/6] Creating the tunnel '$TUNNEL_NAME'..."

if sudo cloudflared tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$TUNNEL_NAME"; then
    echo "Tunnel '$TUNNEL_NAME' already exists — reusing it."
else
    sudo cloudflared tunnel create "$TUNNEL_NAME"
fi

TUNNEL_ID="$(sudo cloudflared tunnel list 2>/dev/null \
    | awk -v n="$TUNNEL_NAME" '$2 == n {print $1; exit}')"

if [ -z "$TUNNEL_ID" ]; then
    echo "ERROR: could not determine the tunnel ID after creating it."
    echo "       Inspect with: sudo cloudflared tunnel list"
    exit 1
fi
echo "Tunnel ID: $TUNNEL_ID"

# ── Step 4: DNS record ────────────────────────────────────────────────
echo ""
echo "[4/6] Pointing $HOSTNAME_ARG at the tunnel..."
# Idempotent in practice; an existing record for this hostname makes it fail,
# which is worth reporting rather than silently overwriting somebody's DNS.
if sudo cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME_ARG"; then
    echo "DNS record created."
else
    echo ""
    echo "NOTE: the DNS route was not created. That is expected if a record for"
    echo "      $HOSTNAME_ARG already exists. Check the Cloudflare dashboard —"
    echo "      it should be a CNAME to ${TUNNEL_ID}.cfargotunnel.com (proxied)."
fi

# ── Step 5: tunnel config + service ───────────────────────────────────
echo ""
echo "[5/6] Writing $CF_CONFIG and installing the service..."

sudo mkdir -p "$CF_DIR"

# The credentials file lands in root's home when created with sudo; the service
# runs as root too, so point at it there.
CRED_FILE="/root/.cloudflared/${TUNNEL_ID}.json"
if ! sudo test -f "$CRED_FILE"; then
    FOUND="$(sudo find / -name "${TUNNEL_ID}.json" -path "*cloudflared*" 2>/dev/null | head -1)"
    [ -n "$FOUND" ] && CRED_FILE="$FOUND"
fi
echo "Credentials: $CRED_FILE"

sudo tee "$CF_CONFIG" >/dev/null <<CFEOF
# Written by scripts/setup-tunnel.sh
tunnel: ${TUNNEL_ID}
credentials-file: ${CRED_FILE}

ingress:
  - hostname: ${HOSTNAME_ARG}
    service: ${LOCAL_URL}
    originRequest:
      # The panel refresh can hold a request for ~20 seconds while the e-paper
      # redraws; the default would cut it off mid-update.
      connectTimeout: 30s
      noTLSVerify: true
  # Every ingress list must end with a catch-all.
  - service: http_status:404
CFEOF

sudo cloudflared --config "$CF_CONFIG" service install 2>/dev/null \
    || echo "Service already installed — reusing it."
sudo systemctl daemon-reload
sudo systemctl enable cloudflared
sudo systemctl restart cloudflared

# ── Step 6: tell Vignette about it ────────────────────────────────────
echo ""
echo "[6/6] Pointing Vignette at https://$HOSTNAME_ARG ..."

if [ -f "$CONFIG_JSON" ]; then
    sudo python3 - "$CONFIG_JSON" "https://$HOSTNAME_ARG" <<'PYEOF'
import json, os, sys, tempfile

path, url = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)

cfg["remote_access_provider"] = "cloudflare"
cfg["remote_public_url"] = url
cfg["remote_access_enabled"] = True

# Same atomic write the app uses: this file holds the WiFi password and the
# admin account, and a truncated one costs a re-pair.
directory = os.path.dirname(path) or "."
fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".tmp")
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, path)
print(f"config.json updated -> {url}")
PYEOF

    # Keep ownership with whoever runs the service.
    OWNER="$(stat -c '%U:%G' "$INSTALL_DIR")"
    sudo chown "$OWNER" "$CONFIG_JSON"
else
    echo "NOTE: $CONFIG_JSON does not exist yet (device not set up)."
    echo "      Set it later in Settings -> Remote Access:"
    echo "         Provider = Cloudflare"
    echo "         Address  = https://$HOSTNAME_ARG"
fi

sudo systemctl restart vignette 2>/dev/null || true

# ── Verify ────────────────────────────────────────────────────────────
echo ""
echo "Waiting for the tunnel to come up..."
sleep 8

echo ""
echo "cloudflared: $(systemctl is-active cloudflared)"
echo "vignette:    $(systemctl is-active vignette)"
echo ""
echo "Checking https://$HOSTNAME_ARG ..."
CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 25 "https://$HOSTNAME_ARG" || echo "000")"
case "$CODE" in
    200|302|301) echo "  HTTP $CODE — reachable." ;;
    000) echo "  No response yet. DNS can take a minute to propagate; try again shortly." ;;
    *)   echo "  HTTP $CODE — reachable, but not the response expected. Check: journalctl -u vignette -n 50" ;;
esac

echo ""
echo "============================================"
echo "  Done."
echo ""
echo "  Your display:  https://$HOSTNAME_ARG"
echo "  This address is fixed — it will not change on reconnect."
echo ""
echo "  Logs:    journalctl -u cloudflared -f"
echo "  Status:  systemctl status cloudflared"
echo "============================================"
