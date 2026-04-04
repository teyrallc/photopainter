#!/bin/bash
# PhotoPainter Installation Script
# For Raspberry Pi Zero 2 W with Waveshare 7.3" e-paper display
#
# Usage: bash scripts/install.sh
# Estimated time: 2-4 hours (mainly model download and compilation)

set -e

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "============================================"
echo "  PhotoPainter Installation"
echo "  Install directory: $INSTALL_DIR"
echo "============================================"

# ── Step 1: System packages ──────────────────────────────────────────
echo ""
echo "[1/7] Installing system packages..."
sudo apt-get update
sudo apt-get -y upgrade
sudo apt-get -y install \
    cmake \
    python3-dev \
    python3-venv \
    python3-pip \
    python3-opencv \
    libopencv-dev \
    git \
    git-lfs \
    imagemagick \
    tmux \
    vim

# ── Step 2: Enable SPI ──────────────────────────────────────────────
echo ""
echo "[2/7] Enabling SPI interface..."
if command -v raspi-config &> /dev/null; then
    sudo raspi-config nonint do_spi 0
    echo "SPI enabled."
else
    echo "WARNING: raspi-config not found. Please enable SPI manually."
fi

# ── Step 3: Set swap ────────────────────────────────────────────────
echo ""
echo "[3/7] Setting swap to 1024MB..."
if [ -f /etc/dphys-swapfile ]; then
    sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
    sudo systemctl restart dphys-swapfile
    echo "Swap set to 1024MB."
else
    echo "WARNING: dphys-swapfile not found. Please set swap manually."
fi

# ── Step 4: Python virtual environment ──────────────────────────────
echo ""
echo "[4/7] Setting up Python virtual environment..."
cd "$INSTALL_DIR"
python3 -m venv venv
. venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

echo "Python packages installed."

# ── Step 5: Build OnnxStream ────────────────────────────────────────
echo ""
echo "[5/7] Building OnnxStream (Stable Diffusion inference engine)..."
cd "$INSTALL_DIR"

if [ ! -d "OnnxStream" ]; then
    git clone https://github.com/vitoplantamura/OnnxStream.git
fi

cd OnnxStream/src
mkdir -p build
cd build
cmake ..
cmake --build . --config Release

echo "OnnxStream built successfully."

# ── Step 6: Download model ──────────────────────────────────────────
echo ""
echo "[6/7] Downloading Stable Diffusion XL Turbo model (~8GB)..."
cd "$INSTALL_DIR"
mkdir -p models
cd models

if [ ! -d "stable-diffusion-xl-turbo-1.0-anyshape-onnxstream" ]; then
    git clone --depth=1 \
        https://huggingface.co/vitoplantamura/stable-diffusion-xl-turbo-1.0-anyshape-onnxstream
    echo "Model downloaded."
else
    echo "Model already exists, skipping."
fi

# ── Step 7: Create output directory ─────────────────────────────────
echo ""
echo "[7/8] Creating output directory..."
cd "$INSTALL_DIR"
mkdir -p output

# ── Step 8: Install systemd service ────────────────────────────────
echo ""
echo "[8/8] Setting up systemd service for auto-start..."

# Create service file with correct paths
SERVICE_FILE="/etc/systemd/system/photopainter.service"
sudo bash -c "cat > $SERVICE_FILE" << SVCEOF
[Unit]
Description=PhotoPainter Web Control Interface
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
sudo systemctl enable photopainter
sudo systemctl start photopainter

echo "Service installed and started."

# Get IP address for display
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "============================================"
echo "  Installation Complete!"
echo "============================================"
echo ""
echo "  Web 介面已啟動並設為開機自動執行！"
echo ""
echo "  從任何裝置存取："
echo "    http://${PI_IP}:5000"
echo ""
echo "  服務管理："
echo "    sudo systemctl status photopainter   # 查看狀態"
echo "    sudo systemctl restart photopainter  # 重啟服務"
echo "    sudo systemctl stop photopainter     # 停止服務"
echo "    journalctl -u photopainter -f        # 查看日誌"
echo ""
echo "  遠端更新程式："
echo "    bash scripts/update.sh               # SSH 更新"
echo "    或在 Web 控制台點擊「遠端更新程式」   # Web 更新"
echo ""
echo "  命令列使用（可選）："
echo "    source venv/bin/activate"
echo "    python src/generate_picture.py output/    # AI 生成"
echo "    python src/display_picture.py output/output.png  # 顯示圖片"
echo ""
