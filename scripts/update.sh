#!/bin/bash
# PhotoPainter Remote Update Script
# Pulls latest code from git and restarts the service.
#
# Usage:
#   bash scripts/update.sh
#   Or triggered via Web API: POST /api/system/update

set -e

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$INSTALL_DIR"

echo "============================================"
echo "  PhotoPainter - Remote Update"
echo "============================================"

# Step 1: Pull latest code
echo "[1/3] Pulling latest code..."
git fetch origin
git pull origin "$(git rev-parse --abbrev-ref HEAD)"

# Step 2: Update Python dependencies
echo "[2/3] Updating Python dependencies..."
if [ -f "venv/bin/pip" ]; then
    venv/bin/pip install -r requirements.txt --quiet
fi

# Step 3: Restart service
echo "[3/3] Restarting PhotoPainter service..."
if systemctl is-active --quiet photopainter; then
    sudo systemctl restart photopainter
    echo "Service restarted."
else
    echo "Service not running. Start with: sudo systemctl start photopainter"
fi

echo ""
echo "Update complete!"
echo "Current version: $(git log --oneline -1)"
