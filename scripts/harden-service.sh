#!/bin/bash
# Vignette — move the service off root, without touching anything else.
#
# Usage:
#   bash scripts/harden-service.sh            # migrate
#   bash scripts/harden-service.sh --check    # preflight only, change nothing
#   bash scripts/harden-service.sh --rollback # restore the previous unit
#
# Why: this process answers on the public internet through the remote-access
# tunnel. It accepts uploads and decodes them with Pillow, serves files from
# disk, and shells out to nmcli. A flaw in any of that, running as root, hands
# over the whole board — every file, the WiFi credentials, and a foothold on
# the LAN behind it. As its own account the same flaw reaches the photo
# library, its config, and four allowlisted commands. Smaller, not zero.
#
# Why not scripts/install.sh: that also runs a full `apt-get upgrade` and
# rebuilds the virtualenv. On a device that is finally working, those are the
# risky parts and none of them are the point. This script changes ownership,
# the sudo allowlist and the unit file. Nothing else.
#
# Safety: the current unit is backed up first, the panel's SPI and GPIO access
# is tested as the new account BEFORE anything is switched, and a service that
# fails to come back is rolled back automatically. A frame that cannot start is
# a frame you have to walk over to.

set -uo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_USER="vignette"
VENV_PYTHON="$INSTALL_DIR/venv/bin/python"

# Overridable only so the write, verify and rollback paths can be exercised off
# a Pi — this script's whole value is that it undoes itself when the service
# does not come back, and that is not something to find out on the device.
SERVICE_FILE="${VIGNETTE_SERVICE_FILE:-/etc/systemd/system/vignette.service}"
SUDOERS_FILE="${VIGNETTE_SUDOERS_FILE:-/etc/sudoers.d/vignette}"

# Panel pins, from lib/waveshare_epd/epdconfig.py. BUSY is the only one read
# rather than driven, so probing it cannot disturb a refresh in progress.
BUSY_PIN=24

MODE="${1:-migrate}"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  [ ok ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }
note() { printf '  [note] %s\n' "$*"; }

# ── Rollback ──────────────────────────────────────────────────────────

latest_backup() {
    ls -1t "${SERVICE_FILE}".bak-* 2>/dev/null | head -1
}

do_rollback() {
    local backup
    backup="$(latest_backup)"
    if [ -z "$backup" ]; then
        bad "No backup found next to $SERVICE_FILE — nothing to roll back to."
        exit 1
    fi
    say "Restoring $backup"
    sudo cp "$backup" "$SERVICE_FILE"
    sudo systemctl daemon-reload
    sudo systemctl restart vignette
    sleep 5
    if systemctl is-active --quiet vignette; then
        ok "Rolled back; the service is running again."
    else
        bad "Rolled back, but the service is still down. Check:"
        say "    journalctl -u vignette -n 50 --no-pager"
        exit 1
    fi
}

case "$MODE" in
    --rollback) do_rollback; exit 0 ;;
    migrate|--check) ;;
    -h|--help)
        awk 'NR == 1 { next }
             /^#/    { sub(/^# ?/, ""); print; next }
             { exit }' "$0"
        exit 0 ;;
    *)  say "Unknown option: $MODE"
        say "Usage: bash $0 [--check | --rollback]"
        exit 1 ;;
esac

# --check stops before anything is written. It cannot probe the panel unless
# the account already exists, and it says so rather than implying an all-clear.
CHECK_ONLY=0
[ "$MODE" = "--check" ] && CHECK_ONLY=1

say "============================================"
say "  Vignette — take the service off root"
say "  Install directory: $INSTALL_DIR"
say "============================================"

# ── Preflight ─────────────────────────────────────────────────────────
# Everything that could make the service fail to start is checked here, while
# the working configuration is still in place and nothing has been changed.

say ""
say "[1/6] Preflight"

FATAL=0

if [ ! -x "$VENV_PYTHON" ]; then
    bad "No interpreter at $VENV_PYTHON"
    FATAL=1
else
    ok "virtualenv found"
fi

for grp in spi gpio netdev; do
    if getent group "$grp" >/dev/null 2>&1; then
        ok "group '$grp' exists"
    else
        note "group '$grp' does not exist on this system; it will be skipped"
    fi
done

# The panel talks over /dev/spidev* and a GPIO character device. Running as
# root made their permissions irrelevant; as an ordinary account they are the
# whole question, and a panel that goes dark after the switch is the failure
# that is hardest to notice remotely.
for node in /dev/spidev0.0 /dev/gpiochip0 /dev/gpiomem; do
    if [ -e "$node" ]; then
        ok "$node  $(stat -c '%A %U:%G' "$node")"
    fi
done
if [ ! -e /dev/spidev0.0 ]; then
    bad "/dev/spidev0.0 is missing — SPI is not enabled. Run: sudo raspi-config nonint do_spi 0"
    FATAL=1
fi

if [ "$FATAL" = 1 ]; then
    say ""
    bad "Preflight failed. Nothing has been changed."
    exit 1
fi

# ── Account ───────────────────────────────────────────────────────────

say ""
say "[2/6] Service account"

if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    ok "user '$SERVICE_USER' already exists"
elif [ "$CHECK_ONLY" = 1 ]; then
    say ""
    note "user '$SERVICE_USER' does not exist yet, so the panel cannot be"
    note "probed as it. Everything checkable without changing anything passed."
    note "Run without --check to create the account and probe for real; it"
    note "still stops before switching if the panel is unreachable."
    exit 0
else
    sudo useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER" \
        && ok "created user '$SERVICE_USER'" \
        || { bad "could not create the account"; exit 1; }
fi

for grp in spi gpio netdev; do
    getent group "$grp" >/dev/null 2>&1 && sudo usermod -aG "$grp" "$SERVICE_USER"
done
ok "groups: $(id -nG "$SERVICE_USER")"

# ── Hardware probe, as the new account ────────────────────────────────
# The decisive test, and it runs before the switch: if the panel cannot be
# reached from this account, the migration stops here with the device still
# working as it was.

say ""
say "[3/6] Reaching the panel as '$SERVICE_USER'"

# Plain `sudo -u` — it calls initgroups() for the target account, so the spi
# and gpio memberships added above are in effect, which is the whole point of
# the probe. Not `-i` (the account's shell is nologin, by design) and not `-g`
# (that would replace exactly the groups being tested).
probe_with_groups() {
    sudo -u "$SERVICE_USER" "$VENV_PYTHON" -c "$1" 2>&1
}

SPI_OUT="$(probe_with_groups '
import spidev
s = spidev.SpiDev()
s.open(0, 0)
s.max_speed_hz = 4000000
s.close()
print("SPI OK")')"
if printf '%s' "$SPI_OUT" | grep -q "SPI OK"; then
    ok "SPI: /dev/spidev0.0 opened"
else
    bad "SPI could not be opened as '$SERVICE_USER':"
    printf '%s\n' "$SPI_OUT" | sed 's/^/         /'
    say ""
    bad "Stopping. Nothing has been changed; the service is still running as before."
    exit 1
fi

GPIO_OUT="$(probe_with_groups "
import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup($BUSY_PIN, GPIO.IN)
GPIO.input($BUSY_PIN)
GPIO.cleanup()
print('GPIO OK')")"
if printf '%s' "$GPIO_OUT" | grep -q "GPIO OK"; then
    ok "GPIO: BUSY pin readable"
else
    bad "GPIO could not be reached as '$SERVICE_USER':"
    printf '%s\n' "$GPIO_OUT" | sed 's/^/         /'
    say ""
    bad "Stopping. Nothing has been changed; the service is still running as before."
    exit 1
fi

if [ "$CHECK_ONLY" = 1 ]; then
    say ""
    ok "Preflight passed — the panel is reachable as '$SERVICE_USER'."
    note "Nothing has been changed. Run without --check to migrate."
    exit 0
fi

# ── Ownership and the sudo allowlist ──────────────────────────────────

say ""
say "[4/6] Ownership and privileges"

# Whoever is running this keeps working in the checkout afterwards — git pull,
# editing files — so they join the group rather than losing access to their own
# directory.
INVOKING_USER="${SUDO_USER:-$(id -un)}"

if ! sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"; then
    bad "Could not give $INSTALL_DIR to '$SERVICE_USER'."
    bad "The service could not write its config or photos. Stopping before"
    bad "the unit is touched — the frame is still running as it was."
    exit 1
fi
sudo chmod -R g+rwX "$INSTALL_DIR" \
    || note "chmod g+rwX did not fully apply; check you can still git pull here"
ok "checkout owned by $SERVICE_USER, group-writable"

if [ "$INVOKING_USER" != "$SERVICE_USER" ] && [ "$INVOKING_USER" != "root" ]; then
    sudo usermod -aG "$SERVICE_USER" "$INVOKING_USER"
    note "$INVOKING_USER added to the '$SERVICE_USER' group — log out and back"
    note "in (or run 'newgrp $SERVICE_USER') before using git here again."
fi

# config.json holds the WiFi password, the session key and the admin hash.
# Group-readable is a step too far for that one file.
if [ -f "$INSTALL_DIR/config.json" ]; then
    sudo chmod 600 "$INSTALL_DIR/config.json"
    ok "config.json restricted to $SERVICE_USER (0600)"
fi

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

if sudo visudo -c -f "$SUDOERS_FILE" >/dev/null 2>&1; then
    ok "sudo allowlist installed and valid"
else
    sudo rm -f "$SUDOERS_FILE"
    bad "The sudoers file failed validation and was removed. Nothing else changed."
    exit 1
fi

# ── The unit ──────────────────────────────────────────────────────────

say ""
say "[5/6] Writing the unit"

if [ -f "$SERVICE_FILE" ]; then
    BACKUP="${SERVICE_FILE}.bak-$(date +%Y%m%d-%H%M%S)"
    sudo cp "$SERVICE_FILE" "$BACKUP"
    ok "current unit backed up to $BACKUP"
else
    BACKUP=""
    note "no existing unit to back up"
fi

# On the hardening below: NoNewPrivileges has to stay off, because the four
# allowlisted actions go through sudo, which is setuid. That single requirement
# rules out the strictest options (a sealed CapabilityBoundingSet, a
# SystemCallFilter without @privileged) — they would break rebooting and
# joining a network. Everything compatible with it is set.
#
# ProcSubset is deliberately *not* set to pid: the interface reports memory and
# CPU temperature, and the device derives its hotspot name from the board
# serial, so /proc/meminfo and /proc/cpuinfo have to stay readable.
sudo bash -c "cat > $SERVICE_FILE" << SVCEOF
[Unit]
Description=Vignette Smart Display
After=network-online.target
Wants=network-online.target

[Service]
Type=simple

# Not root. This process is reachable from the public internet through the
# remote-access tunnel, so a flaw in it must not hand over the whole board.
User=$SERVICE_USER
Group=$SERVICE_USER
SupplementaryGroups=spi gpio netdev

WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/web/app.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

# Required: bringing WiFi up, rebooting and shutting down go through sudo
# against the narrow allowlist in $SUDOERS_FILE, and sudo is setuid.
NoNewPrivileges=no

# Filesystem: /usr, /boot and /etc read-only, /home read-only apart from the
# checkout this service owns. Deliberately not ProtectSystem=strict — that
# would also seal /run and /var for everything in this cgroup, sudo's children
# included, and the marginal gain is not worth debugging a frame that can no
# longer join a network. /etc is the part that matters and full covers it.
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$INSTALL_DIR
PrivateTmp=yes
UMask=0077

# Kernel and host state this service has no business touching.
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible

# Process and memory.
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native

# The panel needs SPI and the GPIO character devices; nothing else. The
# gpiochip number moves between Pi models and kernels, so the range is listed
# rather than guessed — naming one that does not exist is harmless.
#
# If the panel ever goes dark after an OS upgrade, these five lines are the
# first thing to remove: the account's group membership is already doing the
# real work, and this only narrows it further.
DevicePolicy=closed
DeviceAllow=/dev/spidev0.0 rw
DeviceAllow=/dev/spidev0.1 rw
DeviceAllow=char-gpiochip rw
DeviceAllow=/dev/gpiomem rw
DeviceAllow=/dev/gpiomem0 rw

[Install]
WantedBy=multi-user.target
SVCEOF

ok "unit written"

sudo systemctl daemon-reload
sudo systemctl restart vignette

# ── Verify, and undo it if it did not work ────────────────────────────

say ""
say "[6/6] Verifying"
sleep 8

FAILED=0

if systemctl is-active --quiet vignette; then
    ok "service is active as $(systemctl show -p User --value vignette)"
else
    bad "service is not running"
    FAILED=1
fi

if [ "$FAILED" = 0 ]; then
    CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 http://localhost:5000/ 2>/dev/null)"
    case "${CODE:-000}" in
        200|301|302) ok "HTTP $CODE from localhost:5000" ;;
        *)           bad "localhost:5000 answered '${CODE:-nothing}'"; FAILED=1 ;;
    esac
fi

if [ "$FAILED" = 1 ]; then
    say ""
    bad "The service did not come back. Rolling back automatically."
    printf '%s\n' "--- last 30 log lines ---"
    sudo journalctl -u vignette -n 30 --no-pager
    say ""
    if [ -n "$BACKUP" ]; then
        sudo cp "$BACKUP" "$SERVICE_FILE"
        sudo systemctl daemon-reload
        sudo systemctl restart vignette
        sleep 5
        if systemctl is-active --quiet vignette; then
            ok "Rolled back to the previous unit; the frame is working again."
            say ""
            say "  Ownership and the sudo allowlist were left in place — they are"
            say "  harmless on their own. To undo those too:"
            say "      sudo chown -R $INVOKING_USER:$INVOKING_USER $INSTALL_DIR"
            say "      sudo rm -f $SUDOERS_FILE"
        else
            bad "Rollback did not restore it either. Check the log above."
        fi
    fi
    exit 1
fi

# Anything the panel could not reach shows up here rather than in the exit code.
if sudo journalctl -u vignette -b --no-pager | tail -60 \
        | grep -iE "permission denied|not allocated|no access|errno 13" >/dev/null; then
    say ""
    note "The log mentions a permission problem. The service is up, but check"
    note "the panel actually redraws before walking away:"
    note "    sudo journalctl -u vignette -n 60 --no-pager"
    note "    bash $0 --rollback     # if the panel has gone dark"
fi

say ""
say "============================================"
say "  Done. The service no longer runs as root."
say ""
say "  Verify the panel still redraws — open the interface and refresh the"
say "  display. That is the one thing this script cannot check for you."
say ""
say "  Roll back at any time:"
say "      bash $0 --rollback"
say "============================================"
