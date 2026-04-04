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
echo "[7/7] Final setup..."
cd "$INSTALL_DIR"
mkdir -p output

echo ""
echo "============================================"
echo "  Installation Complete!"
echo "============================================"
echo ""
echo "Usage:"
echo ""
echo "  1. Start Web Interface:"
echo "     cd $INSTALL_DIR"
echo "     source venv/bin/activate"
echo "     python web/app.py"
echo "     # Open http://<your-pi-ip>:5000"
echo ""
echo "  2. Generate AI image (CLI):"
echo "     python src/generate_picture.py output/"
echo ""
echo "  3. Display image (CLI):"
echo "     python src/display_picture.py output/output.png"
echo ""
echo "  4. Button controller:"
echo "     python src/display_buttons.py"
echo ""
echo "  5. Auto-start on boot (optional):"
echo "     crontab -e"
echo "     Add: @reboot cd $INSTALL_DIR && venv/bin/python web/app.py &"
echo ""
