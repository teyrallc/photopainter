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

# The Cloudflare zone that ought to own this hostname, and the label to create
# inside it. Right for the ordinary one-subdomain case, which is what the
# README tells people to use; only ever shown as guidance, never relied on.
ZONE="${HOSTNAME_ARG#*.}"
RECORD_NAME="${HOSTNAME_ARG%%.*}"

echo "============================================"
echo "  Vignette — Cloudflare Tunnel setup"
echo "  Hostname: $HOSTNAME_ARG"
echo "  Forwards to: $LOCAL_URL"
echo "============================================"

# ── Step 1: install cloudflared ───────────────────────────────────────
echo ""
echo "[1/6] Installing cloudflared..."

CF_APT_LIST="/etc/apt/sources.list.d/cloudflared.list"

# Cloudflare's repo lags new Debian releases, so a codename it has never heard
# of has to fall back rather than fail. bookworm packages run fine on trixie —
# it is a static Go binary.
cf_codename() {
    local codename
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")"
    case "$codename" in
        bullseye|bookworm|buster|focal|jammy|noble) ;;
        *) codename=bookworm ;;
    esac
    printf '%s' "$codename"
}

write_apt_list() {
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(cf_codename) main" \
        | sudo tee "$CF_APT_LIST" >/dev/null
}

# A run from before that fallback existed leaves a list naming a codename
# Cloudflare never published, and every later `apt-get update` on the machine
# fails on it — not just this repository. Skipping the install below because
# cloudflared is already present would leave that behind forever, so repair it
# whichever branch we take.
repair_stale_apt_list() {
    [ -f "$CF_APT_LIST" ] || return 0
    local want
    want="$(cf_codename)"
    grep -q "cloudflared ${want} main" "$CF_APT_LIST" && return 0
    echo "  Repairing $CF_APT_LIST — it named a release Cloudflare does not publish."
    write_apt_list
}

install_from_apt() {
    sudo mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL --max-time 60 https://pkg.cloudflare.com/cloudflare-main.gpg \
        | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null || return 1

    local codename
    codename="$(cf_codename)"
    if [ "$codename" != "$(. /etc/os-release && echo "${VERSION_CODENAME:-bookworm}")" ]; then
        echo "  (this release is not published by Cloudflare; using $codename)"
    fi
    write_apt_list

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
    repair_stale_apt_list
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
    cat <<LOGINEOF

  ----------------------------------------------------------------
   PICK THE RIGHT DOMAIN ON THE PAGE THAT OPENS:

       $ZONE

  ----------------------------------------------------------------

  A URL will be printed below. Open it in ANY browser — it does not have
  to be on this device — sign in, and authorize the domain named above.

  Authorizing a *different* domain does not fail. cloudflared would go on
  to create '$HOSTNAME_ARG.<that-other-domain>' instead, and
  $HOSTNAME_ARG would never resolve. Step 4 checks for this and stops.

LOGINEOF
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

manual_dns_instructions() {
    cat <<DNSEOF

  To add the record by hand: Cloudflare dashboard -> zone $ZONE -> DNS ->
  Add record

      Type    CNAME
      Name    $RECORD_NAME
      Target  ${TUNNEL_ID}.cfargotunnel.com
      Proxy   Proxied (orange cloud) — DNS only will not work

DNSEOF
}

# Three outcomes that have to be told apart. Two of them used to print the same
# reassuring line, so a setup that had not worked at all looked finished:
#
#   * created for the hostname we asked for   -> done
#   * a record already exists                 -> fine, but say which
#   * created under some *other* zone         -> the login authorized the wrong
#     domain, so cloudflared treated our fully-qualified hostname as a name
#     relative to that zone and appended it. Nothing will ever resolve.
ROUTE_LOG="$(sudo cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME_ARG" 2>&1)" \
    && ROUTE_OK=1 || ROUTE_OK=0
printf '%s\n' "$ROUTE_LOG"

# e.g. "INF Added CNAME yilin.example.com which will route to this tunnel ..."
ADDED="$(printf '%s\n' "$ROUTE_LOG" \
    | sed -n 's/.*Added CNAME \([^ ][^ ]*\) which will route.*/\1/p' | head -1)"

if [ -n "$ADDED" ] && [ "$ADDED" != "$HOSTNAME_ARG" ]; then
    cat <<WRONGZONEEOF

ERROR: the record was created as

           $ADDED

       but the hostname you asked for is

           $HOSTNAME_ARG

  cloudflared is authorized for a different Cloudflare zone than the one that
  owns this hostname, so it created the record inside that zone instead.
  $HOSTNAME_ARG will not resolve.

  To fix it: delete the record above in the Cloudflare dashboard, then

      sudo rm -f /root/.cloudflared/cert.pem
      sudo cloudflared tunnel login       # authorize: $ZONE
      bash $0 $HOSTNAME_ARG

  Deleting cert.pem re-authorizes the account only. The tunnel and its
  credentials are untouched, so nothing has to be rebuilt.
WRONGZONEEOF
    manual_dns_instructions
    exit 1
fi

if [ "$ROUTE_OK" = 1 ]; then
    echo "DNS record created for $HOSTNAME_ARG."
elif printf '%s' "$ROUTE_LOG" | grep -qi 'already exists'; then
    echo "A record for $HOSTNAME_ARG already exists — leaving it alone."
    echo "Confirm in the dashboard that it is a CNAME to"
    echo "${TUNNEL_ID}.cfargotunnel.com, proxied."
else
    echo ""
    echo "ERROR: the DNS record could not be created (see the output above)."
    manual_dns_instructions
    exit 1
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

# Resolve before dialling. A name that is simply not known here yet is not an
# HTTP failure, and reporting it as one sent people looking at the wrong logs.
if ! getent hosts "$HOSTNAME_ARG" >/dev/null 2>&1; then
    cat <<UNRESOLVEDEOF
  $HOSTNAME_ARG does not resolve from this device yet.

  The record was just created. A resolver that already answered "no such
  name" for it — likely, if you tried the address before running this —
  keeps that answer for up to 30 minutes.

  The tunnel is up regardless. Retry with:
      getent hosts $HOSTNAME_ARG
  or check now from a phone on mobile data, which uses a different resolver.
UNRESOLVEDEOF
else
    # `curl -w '%{http_code}'` prints 000 on a connection failure *and* exits
    # non-zero. The old `|| echo "000"` appended a second one, so the variable
    # held '000000', matched no case, and a device that never answered was
    # reported as "reachable".
    CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 25 \
            "https://$HOSTNAME_ARG" 2>/dev/null)" || true
    case "${CODE:-000}" in
        200|301|302)
            echo "  HTTP $CODE — reachable." ;;
        000)
            echo "  Resolved, but nothing answered in time."
            echo "  Check: journalctl -u cloudflared -n 50" ;;
        502|503|504)
            echo "  HTTP $CODE — the tunnel is up, but $LOCAL_URL is not answering."
            echo "  Check: systemctl status vignette; journalctl -u vignette -n 50" ;;
        *)
            echo "  HTTP $CODE — unexpected. Check: journalctl -u vignette -n 50" ;;
    esac
fi

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
