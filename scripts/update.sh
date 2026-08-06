#!/bin/bash
# Vignette — update to the latest code.
#
# Usage:
#   bash scripts/update.sh                 # over SSH
#   Settings → Update Software             # from the web console
#
# The service restart is deliberately handed to a *detached* systemd unit.
# When this script runs from the web console it is a child of vignette.service,
# and `systemctl restart vignette` kills the whole service cgroup — this script
# included, along with the HTTP request waiting on it. The update would land but
# always look like it had failed.

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$INSTALL_DIR"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

echo "============================================"
echo "  Vignette — Update"
echo "============================================"
echo "Directory: $INSTALL_DIR"

# ── Which branch are we on? ─────────────────────────────────────────────
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" = "HEAD" ]; then
    echo
    echo "ERROR: this checkout is in a detached HEAD state, so there is no"
    echo "       branch to update from. Pick one first, e.g.:"
    echo "           git checkout main"
    exit 1
fi

echo "Branch:    $BRANCH"
echo "Installed: $(git log --oneline -1)"
echo

# ── Refuse to clobber local edits ───────────────────────────────────────
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: there are uncommitted changes to tracked files, so updating"
    echo "       would overwrite them. Review them with:"
    echo "           git status"
    echo "       Then either commit them, or discard them with:"
    echo "           git checkout -- ."
    echo
    git status --short
    exit 1
fi

# ── Step 1: fetch and fast-forward ──────────────────────────────────────
echo "[1/3] Fetching from origin..."
git fetch --prune origin

BEFORE="$(git rev-parse HEAD)"

if ! git merge --ff-only "origin/$BRANCH"; then
    echo
    echo "ERROR: cannot fast-forward $BRANCH onto origin/$BRANCH."
    echo "       The device has commits that origin does not, so this needs a"
    echo "       human. Inspect with:"
    echo "           git log --oneline HEAD ^origin/$BRANCH"
    exit 1
fi

AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    echo
    echo "Already up to date — nothing to install, no restart needed."
    exit 0
fi

echo
echo "Applied:"
git --no-pager log --oneline "$BEFORE..$AFTER"
echo

# ── Step 2: dependencies ────────────────────────────────────────────────
echo "[2/3] Updating Python dependencies..."
if [ -x venv/bin/pip ]; then
    # Only reinstall when the requirements actually moved: pip on a Pi Zero is
    # slow enough that skipping it saves most of the update.
    if git diff --name-only "$BEFORE" "$AFTER" | grep -qx "requirements.txt"; then
        venv/bin/pip install -r requirements.txt --quiet
        echo "Dependencies updated."
    else
        echo "requirements.txt unchanged — skipped."
    fi
else
    echo "WARNING: venv/bin/pip not found. Dependencies were not updated."
fi
echo

# ── Step 3: restart, detached from this process ─────────────────────────
echo "[3/3] Restarting the Vignette service..."

restart_detached() {
    # A transient unit lives outside vignette.service's cgroup, so the restart
    # cannot take this script (or the web request) down with it. The delay
    # gives the HTTP response time to reach the browser first.
    if command -v systemd-run >/dev/null 2>&1; then
        $SUDO systemd-run --collect --quiet \
            --unit="vignette-restart-$$" \
            --description="Restart Vignette after an update" \
            /bin/sh -c 'sleep 3; systemctl restart vignette' && return 0
    fi
    # Fallback for systems without systemd-run: a new session detaches the
    # child from this process group.
    $SUDO setsid nohup /bin/sh -c 'sleep 3; systemctl restart vignette' \
        >/dev/null 2>&1 &
}

if $SUDO systemctl is-enabled --quiet vignette 2>/dev/null || \
   $SUDO systemctl is-active --quiet vignette 2>/dev/null; then
    restart_detached
    echo "Restart scheduled — the service comes back in a few seconds."
else
    echo "vignette.service is not installed or running."
    echo "Start it with: sudo systemctl start vignette"
fi

echo
echo "Update complete."
echo "Now running: $(git log --oneline -1)"
