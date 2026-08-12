#!/bin/bash
# Vignette — optional extras for the standalone tools in src/
#
# The web service does not need any of this. These are the pieces the CLI
# tools have always assumed were present but that nothing ever installed:
#
#   --vision    OpenCV + numpy, for saliency-aware cropping
#               (src/display_picture.py)
#   --buttons   libgpiod v1 Python bindings, for the GPIO buttons
#               (src/display_buttons.py)
#   --ai        OnnxStream build + Stable Diffusion XL Turbo weights
#               (src/generate_picture.py)
#   --all       all three
#
# Usage:
#   bash scripts/install-extras.sh --vision
#   bash scripts/install-extras.sh --all --yes
#
# BE AWARE, before choosing --ai:
#   * It needs roughly 6 GB of free disk.
#   * Building XNNPACK on a Pi Zero 2 W takes hours and will fail without
#     swap. The script checks and tells you.
#   * Generating one 800x480 image then takes 20-40 minutes on that board.
#     This is a background curiosity, not an interactive feature.

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$INSTALL_DIR"

VENV_PY="$INSTALL_DIR/venv/bin/python"
VENV_PIP="$INSTALL_DIR/venv/bin/pip"

DO_VISION=0
DO_BUTTONS=0
DO_AI=0
ASSUME_YES=0

MODEL_REPO="https://huggingface.co/vitoplantamura/stable-diffusion-xl-turbo-1.0-anyshape-onnxstream"
MODEL_DIR="$INSTALL_DIR/models/stable-diffusion-xl-turbo-1.0-anyshape-onnxstream"
ONNXSTREAM_REPO="https://github.com/vitoplantamura/OnnxStream.git"
ONNXSTREAM_DIR="$INSTALL_DIR/OnnxStream"
SD_BINARY="$ONNXSTREAM_DIR/src/build/sd"

usage() {
    # Print the header comment as the help text, stopping at the first line
    # that is not a comment. A hardcoded line range silently starts leaking
    # code into the help the moment the header is edited.
    awk 'NR == 1 { next }
         /^#/    { sub(/^# ?/, ""); print; next }
         { exit }' "$0"
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --vision)  DO_VISION=1 ;;
        --buttons) DO_BUTTONS=1 ;;
        --ai)      DO_AI=1 ;;
        --all)     DO_VISION=1; DO_BUTTONS=1; DO_AI=1 ;;
        --yes|-y)  ASSUME_YES=1 ;;
        --help|-h) usage 0 ;;
        *) echo "Unknown option: $1"; echo; usage 1 ;;
    esac
    shift
done

if [ $((DO_VISION + DO_BUTTONS + DO_AI)) -eq 0 ]; then
    usage 1
fi

if [ ! -x "$VENV_PY" ]; then
    echo "ERROR: no virtualenv at $INSTALL_DIR/venv."
    echo "       Run 'bash scripts/install.sh' first."
    exit 1
fi

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    read -r -p "$1 [y/N] " answer
    case "$answer" in [yY]*) return 0 ;; *) return 1 ;; esac
}

free_mb() {
    df -Pm "$INSTALL_DIR" | awk 'NR==2 {print $4}'
}

# The service account owns the checkout after scripts/install.sh, so anything
# written here has to end up owned by it too or the frame cannot read it.
own_for_service() {
    if id -u vignette >/dev/null 2>&1; then
        sudo chown -R vignette:vignette "$1" 2>/dev/null || true
    fi
}

echo "============================================"
echo "  Vignette — optional extras"
echo "  Directory: $INSTALL_DIR"
echo "  Free disk: $(free_mb) MB"
echo "============================================"

# ── Vision: OpenCV + numpy ────────────────────────────────────────────
if [ "$DO_VISION" -eq 1 ]; then
    echo ""
    echo "── Vision (OpenCV + numpy) ─────────────────────────────────"

    if [ "$(free_mb)" -lt 800 ]; then
        echo "ERROR: less than 800 MB free; OpenCV needs room to unpack."
        exit 1
    fi

    # libopenblas is what the numpy ARM wheels link against; without it the
    # import fails with a missing-shared-object error that looks like a numpy
    # bug rather than a missing system package.
    sudo apt-get update
    sudo apt-get -y install libopenblas0 libjpeg62-turbo libtiff6 || \
        sudo apt-get -y install libopenblas-base libjpeg62-turbo libtiff5

    echo "Installing Python packages (this is slow on a Pi — be patient)..."
    "$VENV_PIP" install -r "$INSTALL_DIR/requirements-extras.txt"

    echo -n "Verifying: "
    "$VENV_PY" - <<'PY'
import cv2, numpy
assert hasattr(cv2, "saliency"), (
    "cv2.saliency is missing — the plain opencv-python package was installed "
    "instead of opencv-contrib-python-headless.")
cv2.saliency.StaticSaliencySpectralResidual_create()
print(f"OpenCV {cv2.__version__} with saliency, numpy {numpy.__version__} — OK")
PY
    echo "Vision extras installed."
    echo "Try:  venv/bin/python src/display_picture.py photo.jpg -s -q -o /tmp/crop.png"
fi

# ── Buttons: libgpiod v1 bindings ─────────────────────────────────────
if [ "$DO_BUTTONS" -eq 1 ]; then
    echo ""
    echo "── Buttons (libgpiod) ──────────────────────────────────────"

    sudo apt-get update
    sudo apt-get -y install python3-libgpiod gpiod

    # The bindings are a system package, so the venv can only see them if it
    # was built with --system-site-packages. Report rather than silently
    # rebuilding somebody's environment.
    if "$VENV_PY" -c "import gpiod" 2>/dev/null; then
        echo "The venv can see gpiod."
    else
        echo ""
        echo "NOTE: python3-libgpiod is installed system-wide, but this venv"
        echo "      cannot see it. Either run the button script with the system"
        echo "      interpreter:"
        echo "          python3 src/display_buttons.py"
        echo "      or recreate the venv with:"
        echo "          python3 -m venv --system-site-packages venv"
    fi

    echo -n "Checking API version: "
    python3 - <<'PY'
import sys
try:
    import gpiod
except ImportError:
    sys.exit("python3-libgpiod did not import — check the apt install above.")
if hasattr(gpiod, "LINE_REQ_EV_FALLING_EDGE"):
    print("libgpiod v1 — matches src/display_buttons.py")
else:
    sys.exit("This is libgpiod v2, whose API differs. src/display_buttons.py "
             "needs the v1 bindings that Debian ships as python3-libgpiod.")
PY

    echo ""
    echo "The buttons are NOT started automatically — there is no systemd unit"
    echo "for them by design. Run them by hand, or add a unit if you want them"
    echo "always on:"
    echo "    python3 src/display_buttons.py"
fi

# ── AI: OnnxStream + SDXL Turbo weights ───────────────────────────────
if [ "$DO_AI" -eq 1 ]; then
    echo ""
    echo "── AI image generation (OnnxStream + SDXL Turbo) ───────────"

    NEEDED_MB=6000
    if [ "$(free_mb)" -lt "$NEEDED_MB" ]; then
        echo "ERROR: needs about ${NEEDED_MB} MB free, found $(free_mb) MB."
        echo "       The model weights alone are roughly 2.5 GB."
        exit 1
    fi

    TOTAL_RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
    SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
    echo "RAM: ${TOTAL_RAM_MB} MB   Swap: ${SWAP_MB} MB"

    if [ $((TOTAL_RAM_MB + SWAP_MB)) -lt 2048 ]; then
        echo ""
        echo "WARNING: building XNNPACK needs about 2 GB of RAM+swap and this"
        echo "         machine has $((TOTAL_RAM_MB + SWAP_MB)) MB. The compile will"
        echo "         very likely be killed part-way through."
        echo ""
        echo "         Raise the swap file first:"
        echo "             sudo dphys-swapfile swapoff"
        echo "             sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' \\"
        echo "                 /etc/dphys-swapfile"
        echo "             sudo dphys-swapfile setup && sudo dphys-swapfile swapon"
        echo ""
        confirm "Continue anyway?" || exit 1
    fi

    echo ""
    echo "This step takes hours on a Pi Zero 2 W. Running it inside tmux or"
    echo "screen is strongly recommended so an SSH drop does not kill it."
    confirm "Proceed?" || exit 1

    sudo apt-get update
    sudo apt-get -y install cmake build-essential git git-lfs

    # ── Build OnnxStream ──────────────────────────────────────────
    if [ -x "$SD_BINARY" ]; then
        echo "OnnxStream is already built — skipping. Delete $ONNXSTREAM_DIR to rebuild."
    else
        echo ""
        echo "[1/3] Fetching XNNPACK and OnnxStream..."
        if [ ! -d "$ONNXSTREAM_DIR" ]; then
            git clone --depth 1 "$ONNXSTREAM_REPO" "$ONNXSTREAM_DIR"
        fi

        XNNPACK_DIR="$INSTALL_DIR/XNNPACK"
        if [ ! -d "$XNNPACK_DIR" ]; then
            git clone https://github.com/google/XNNPACK.git "$XNNPACK_DIR"
            # OnnxStream pins a known-good XNNPACK commit in its README; a
            # fresh master regularly fails to build against it.
            git -C "$XNNPACK_DIR" checkout 579de32260742a24166ecd13213d2e60af862675
        fi

        echo ""
        echo "[2/3] Building XNNPACK (the long part)..."
        mkdir -p "$XNNPACK_DIR/build"
        cmake -S "$XNNPACK_DIR" -B "$XNNPACK_DIR/build" \
            -DCMAKE_BUILD_TYPE=Release \
            -DXNNPACK_BUILD_TESTS=OFF \
            -DXNNPACK_BUILD_BENCHMARKS=OFF
        # One job: parallel compiles are what push a 512 MB board into the OOM
        # killer, and a build that dies at 90% has cost more than it saved.
        cmake --build "$XNNPACK_DIR/build" -j1

        echo ""
        echo "[3/3] Building the OnnxStream sd example..."
        mkdir -p "$ONNXSTREAM_DIR/src/build"
        cmake -S "$ONNXSTREAM_DIR/src" -B "$ONNXSTREAM_DIR/src/build" \
            -DMAX_SPEED=ON \
            -DXNNPACK_DIR="$XNNPACK_DIR" \
            -DCMAKE_BUILD_TYPE=Release
        cmake --build "$ONNXSTREAM_DIR/src/build" -j1

        if [ ! -x "$SD_BINARY" ]; then
            echo "ERROR: the build finished but $SD_BINARY is missing."
            exit 1
        fi
        echo "Built $SD_BINARY"
    fi

    # ── Model weights ─────────────────────────────────────────────
    if [ -d "$MODEL_DIR" ]; then
        echo "Model weights already present — skipping."
    else
        echo ""
        echo "Downloading SDXL Turbo weights (~2.5 GB)..."
        git lfs install --skip-repo
        mkdir -p "$INSTALL_DIR/models"
        git clone --depth 1 "$MODEL_REPO" "$MODEL_DIR"
    fi

    own_for_service "$INSTALL_DIR/models"
    own_for_service "$ONNXSTREAM_DIR"

    echo ""
    echo "AI extras installed."
    echo "Try:  venv/bin/python src/generate_picture.py output/ --steps 5"
    echo "      (20-40 minutes per image on a Pi Zero 2 W)"
fi

echo ""
echo "============================================"
echo "  Done."
echo "============================================"
