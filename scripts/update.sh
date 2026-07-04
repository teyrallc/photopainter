#!/bin/bash
# Vignette Remote Update Script
# Pulls latest code from git and restarts the service.
#
# Usage:
#   bash scripts/update.sh
#   Or triggered via Web API: POST /api/system/update

set -e

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$INSTALL_DIR"

echo "============================================"
echo "  Vignette - Remote Update"
echo "============================================"

# Trust this repo even if ownership looks odd to git (defensive).
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# Step 1: Fetch + hard-reset to the remote branch. For an appliance this is more
# predictable than `git pull`: it never leaves the repo in a conflicted state.
# config.json is gitignored, so device settings are preserved. Local edits to
# TRACKED files are intentionally discarded.
echo "[1/3] Fetching latest code (branch: $BRANCH)..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

# Step 2: Update Python dependencies
echo "[2/3] Updating Python dependencies..."
if [ -f "venv/bin/pip" ]; then
    venv/bin/pip install -r requirements.txt --quiet
fi

# Step 3: Restart the service without blocking.
# When triggered from the web UI this script runs inside the vignette service's
# cgroup. `--no-block` queues the restart with systemd (PID 1) and returns
# immediately, so the restart job — which reloads the new code — is driven by
# systemd itself and completes even though stopping the unit tears down this
# script's cgroup. This exact command is what install.sh whitelists in sudoers,
# so it also works under the non-root service user.
# (git pull + pip already finished above, so nothing is lost when the cgroup
# is torn down; the web UI polls /api/system/update/status and simply reloads
# when the connection drops on restart.)
echo "[3/3] Scheduling service restart..."
if systemctl is-active --quiet vignette; then
    sudo systemctl restart --no-block vignette
    echo "Service restart scheduled."
else
    echo "Service not running. Start with: sudo systemctl start vignette"
fi

echo ""
echo "Update complete!"
echo "Current version: $(git log --oneline -1)"
