#!/usr/bin/env python3
"""
Vignette - H System Smart Display Web Control Interface
Flask web application for controlling the Waveshare 7.3" e-paper display.
"""

import io
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import (Flask, jsonify, render_template, request,
                   send_file, send_from_directory)
from PIL import Image, ImageDraw, ImageFont

# Project paths
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
LIB_DIR = os.path.join(PROJECT_DIR, "lib")

# Add lib to path for waveshare_epd
sys.path.insert(0, LIB_DIR)

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("vignette")

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Display lock - only one display operation at a time
display_lock = threading.Lock()

# Display state
display_state = {
    "current_image": None,
    "last_update": None,
    "status": "idle",  # idle, displaying, error
}

# Photo navigation state
photo_state = {
    "current_index": -1,  # -1 = latest
    "current_image": None,
    "total": 0,
}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'gif'}
EPD_WIDTH = 800
EPD_HEIGHT = 480

# 6-color palette for e-paper simulation (epd7in3e panel)
EPAPER_PALETTE = (
    0, 0, 0,        # Black   (index 0)
    255, 255, 255,   # White   (index 1)
    255, 255, 0,     # Yellow  (index 2)
    255, 0, 0,       # Red     (index 3)
    0, 0, 0,         # (unused, index 4)
    0, 0, 255,       # Blue    (index 5)
    0, 255, 0,       # Green   (index 6)
) + (0, 0, 0) * 249


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_image_list():
    """Get list of images sorted by modification time (newest first)."""
    images = []
    for ext in ALLOWED_EXTENSIONS:
        for f in Path(OUTPUT_DIR).glob(f"*.{ext}"):
            stat = f.stat()
            images.append({
                "filename": f.name,
                "path": str(f),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "modified_ts": stat.st_mtime,
            })
    images.sort(key=lambda x: x["modified_ts"], reverse=True)
    photo_state["total"] = len(images)
    return images


def quantize_to_epaper(image_path):
    """Quantize an image to 7-color e-paper palette, return as PNG bytes."""
    img = Image.open(image_path).convert("RGB")
    img = img.resize((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)

    pal_image = Image.new("P", (1, 1))
    pal_image.putpalette(EPAPER_PALETTE)

    img_quantized = img.quantize(palette=pal_image)
    img_rgb = img_quantized.convert("RGB")

    buf = io.BytesIO()
    img_rgb.save(buf, format='PNG')
    buf.seek(0)
    return buf


def process_upload(file_storage):
    """Process an uploaded image: save and create display-ready version."""
    from werkzeug.utils import secure_filename

    filename = secure_filename(file_storage.filename)
    if not filename:
        filename = f"upload_{int(time.time())}.png"

    base, ext = os.path.splitext(filename)
    filepath = os.path.join(OUTPUT_DIR, filename)
    counter = 1
    while os.path.exists(filepath):
        filename = f"{base}_{counter}{ext}"
        filepath = os.path.join(OUTPUT_DIR, filename)
        counter += 1

    file_storage.save(filepath)

    # Resize to display dimensions
    img = Image.open(filepath).convert("RGB")
    img_resized = img.resize((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)
    img_resized.save(filepath)

    return filename


# ── E-Paper Display Functions ──────────────────────────────────────────────

def display_image_on_epaper(image_path):
    """Display image on e-paper using direct driver import."""
    display_state["status"] = "displaying"
    display_state["current_image"] = os.path.basename(image_path)
    logger.info(f"Displaying image: {image_path}")

    try:
        from waveshare_epd import epd7in3e

        img = Image.open(image_path).convert("RGB")
        img = img.resize((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)

        epd = epd7in3e.EPD()
        logger.info("EPD init...")
        epd.init()

        logger.info("Sending image buffer...")
        buf = epd.getbuffer(img)
        epd.display(buf)

        logger.info("EPD sleep...")
        epd.sleep()

        display_state["status"] = "idle"
        display_state["last_update"] = datetime.now().isoformat()
        logger.info("Display update complete!")
        return True, "OK"

    except Exception as e:
        logger.error(f"Display failed: {e}", exc_info=True)
        display_state["status"] = "error"
        return False, str(e)


def display_test_pattern():
    """Send a test pattern to e-paper to verify hardware."""
    logger.info("Sending test pattern...")
    try:
        from waveshare_epd import epd7in3e

        epd = epd7in3e.EPD()
        epd.init()

        # Create test image with color bars
        img = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        colors = [
            ((0, 0, 0), "Black"),
            ((255, 255, 255), "White"),
            ((0, 255, 0), "Green"),
            ((0, 0, 255), "Blue"),
            ((255, 0, 0), "Red"),
            ((255, 255, 0), "Yellow"),
            ((255, 128, 0), "Orange"),
        ]
        bar_width = EPD_WIDTH // len(colors)
        for i, (color, name) in enumerate(colors):
            x0 = i * bar_width
            x1 = (i + 1) * bar_width
            draw.rectangle([x0, 0, x1, EPD_HEIGHT], fill=color)
            text_color = (255, 255, 255) if color in [(0, 0, 0), (0, 0, 255)] else (0, 0, 0)
            draw.text((x0 + 10, EPD_HEIGHT // 2), name, fill=text_color)

        # Add header
        draw.rectangle([0, 0, EPD_WIDTH, 40], fill=(0, 0, 0))
        draw.text((10, 10), "Vignette - E-Paper Test Pattern", fill=(255, 255, 255))

        buf = epd.getbuffer(img)
        epd.display(buf)
        epd.sleep()

        display_state["status"] = "idle"
        display_state["current_image"] = "[test pattern]"
        display_state["last_update"] = datetime.now().isoformat()
        logger.info("Test pattern displayed!")
        return True, "OK"

    except Exception as e:
        logger.error(f"Test pattern failed: {e}", exc_info=True)
        return False, str(e)


# ── Photo Navigation ──────────────────────────────────────────────────────

def navigate_photo(direction):
    """Navigate to next/prev/latest photo and display it."""
    images = get_image_list()
    if not images:
        return False, "No images available"

    total = len(images)

    if direction == "latest":
        photo_state["current_index"] = 0
    elif direction == "next":
        idx = photo_state["current_index"]
        photo_state["current_index"] = (idx + 1) % total if idx >= 0 else 1 % total
    elif direction == "prev":
        idx = photo_state["current_index"]
        photo_state["current_index"] = (idx - 1) % total if idx > 0 else total - 1
    elif isinstance(direction, int):
        if 0 <= direction < total:
            photo_state["current_index"] = direction
        else:
            return False, f"Index out of range (0-{total-1})"

    idx = photo_state["current_index"]
    image = images[idx]
    photo_state["current_image"] = image["filename"]
    photo_state["total"] = total

    filepath = os.path.join(OUTPUT_DIR, image["filename"])
    return display_image_on_epaper(filepath)


# ── Page Routes ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Dashboard page."""
    images = get_image_list()
    return render_template('index.html',
                           images=images[:5],
                           display_state=display_state,
                           photo_state=photo_state,
                           total_images=len(images))


@app.route('/upload')
def upload_page():
    return render_template('upload.html')


@app.route('/gallery')
def gallery_page():
    images = get_image_list()
    return render_template('gallery.html', images=images)


@app.route('/manual')
def manual_page():
    return render_template('manual.html')


# ── API: Image Management ─────────────────────────────────────────────────

@app.route('/api/upload', methods=['POST'])
def api_upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed. Use: png, jpg, jpeg, bmp, gif"}), 400

    filename = process_upload(file)
    return jsonify({"success": True, "filename": filename,
                    "message": f"Image uploaded: {filename}"})


@app.route('/api/display', methods=['POST'])
def api_display():
    """Display a specific image on e-paper."""
    data = request.get_json() or {}
    filename = data.get('filename') or request.form.get('filename')

    if not filename:
        return jsonify({"error": "No filename provided"}), 400

    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Image not found"}), 404

    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503

    try:
        success, msg = display_image_on_epaper(filepath)
        if success:
            # Update photo state to match
            images = get_image_list()
            for i, img in enumerate(images):
                if img["filename"] == filename:
                    photo_state["current_index"] = i
                    photo_state["current_image"] = filename
                    break
            return jsonify({"success": True, "message": "Image displayed"})
        else:
            return jsonify({"error": f"Display failed: {msg}"}), 500
    finally:
        display_lock.release()


@app.route('/api/preview/<filename>')
def api_preview(filename):
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Image not found"}), 404

    buf = quantize_to_epaper(filepath)
    return send_file(buf, mimetype='image/png',
                     download_name=f"preview_{filename}")


@app.route('/api/images')
def api_images():
    return jsonify(get_image_list())


@app.route('/api/images/<filename>', methods=['DELETE'])
def api_delete_image(filename):
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Image not found"}), 404

    os.remove(filepath)
    return jsonify({"success": True, "message": f"Deleted {filename}"})


# ── API: Photo Navigation (virtual buttons) ───────────────────────────────

@app.route('/api/photo/current')
def api_photo_current():
    """Get current photo info and navigation state."""
    images = get_image_list()
    photo_state["total"] = len(images)
    return jsonify({
        "index": photo_state["current_index"],
        "filename": photo_state["current_image"],
        "total": photo_state["total"],
    })


@app.route('/api/photo/next', methods=['POST'])
def api_photo_next():
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = navigate_photo("next")
        if success:
            return jsonify({"success": True, "photo": photo_state})
        return jsonify({"error": msg}), 500
    finally:
        display_lock.release()


@app.route('/api/photo/prev', methods=['POST'])
def api_photo_prev():
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = navigate_photo("prev")
        if success:
            return jsonify({"success": True, "photo": photo_state})
        return jsonify({"error": msg}), 500
    finally:
        display_lock.release()


@app.route('/api/photo/latest', methods=['POST'])
def api_photo_latest():
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = navigate_photo("latest")
        if success:
            return jsonify({"success": True, "photo": photo_state})
        return jsonify({"error": msg}), 500
    finally:
        display_lock.release()


@app.route('/api/photo/goto/<int:idx>', methods=['POST'])
def api_photo_goto(idx):
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = navigate_photo(idx)
        if success:
            return jsonify({"success": True, "photo": photo_state})
        return jsonify({"error": msg}), 500
    finally:
        display_lock.release()


# ── API: Display Control ──────────────────────────────────────────────────

@app.route('/api/display/test', methods=['POST'])
def api_display_test():
    """Send test pattern to e-paper."""
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = display_test_pattern()
        if success:
            return jsonify({"success": True, "message": "Test pattern displayed"})
        return jsonify({"error": f"Test failed: {msg}"}), 500
    finally:
        display_lock.release()


@app.route('/api/clear', methods=['POST'])
def api_clear():
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        from waveshare_epd import epd7in3e
        epd = epd7in3e.EPD()
        epd.init()
        epd.Clear()
        epd.sleep()
        display_state["status"] = "idle"
        display_state["current_image"] = None
        return jsonify({"success": True, "message": "Display cleared"})
    except Exception as e:
        return jsonify({"error": f"Clear failed: {e}"}), 500
    finally:
        display_lock.release()


@app.route('/api/sleep', methods=['POST'])
def api_sleep():
    try:
        from waveshare_epd import epd7in3e
        epd = epd7in3e.EPD()
        epd.init()
        epd.sleep()
        display_state["status"] = "sleeping"
        return jsonify({"success": True, "message": "Display sleeping"})
    except Exception as e:
        return jsonify({"error": f"Sleep failed: {e}"}), 500


# ── API: System Status & Management ───────────────────────────────────────

@app.route('/api/status')
def api_status():
    return jsonify({
        "display": display_state,
        "photo": photo_state,
        "total_images": len(get_image_list()),
        "system": get_system_info(),
    })


def get_system_info():
    info = {
        "hostname": "", "ip_addresses": [], "cpu_temp": None,
        "mem_total_mb": None, "mem_available_mb": None,
        "disk_free_gb": None, "uptime": None, "git_version": None,
    }
    try:
        info["hostname"] = subprocess.check_output(
            ["hostname"], text=True, timeout=5).strip()
    except Exception:
        pass
    try:
        output = subprocess.check_output(
            ["hostname", "-I"], text=True, timeout=5).strip()
        info["ip_addresses"] = output.split()
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            info["cpu_temp"] = round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    info["mem_total_mb"] = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    info["mem_available_mb"] = int(line.split()[1]) // 1024
    except Exception:
        pass
    try:
        stat = os.statvfs(PROJECT_DIR)
        info["disk_free_gb"] = round((stat.f_bavail * stat.f_frsize) / (1024**3), 1)
    except Exception:
        pass
    try:
        with open("/proc/uptime") as f:
            secs = int(float(f.read().split()[0]))
            h, r = divmod(secs, 3600)
            m, s = divmod(r, 60)
            info["uptime"] = f"{h}h {m}m {s}s"
    except Exception:
        pass
    try:
        info["git_version"] = subprocess.check_output(
            ["git", "-C", PROJECT_DIR, "log", "--oneline", "-1"],
            text=True, timeout=5).strip()
    except Exception:
        pass
    return info


@app.route('/api/system/info')
def api_system_info():
    return jsonify(get_system_info())


@app.route('/api/system/update', methods=['POST'])
def api_system_update():
    update_script = os.path.join(PROJECT_DIR, "scripts", "update.sh")
    if not os.path.exists(update_script):
        return jsonify({"error": "Update script not found"}), 500
    try:
        result = subprocess.run(
            ["bash", update_script],
            capture_output=True, text=True, timeout=300, cwd=PROJECT_DIR)
        return jsonify({
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr if result.returncode != 0 else None,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Update timed out"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/system/reboot', methods=['POST'])
def api_system_reboot():
    try:
        subprocess.Popen(["sudo", "reboot"])
        return jsonify({"success": True, "message": "Rebooting..."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/system/shutdown', methods=['POST'])
def api_system_shutdown():
    try:
        subprocess.Popen(["sudo", "shutdown", "-h", "now"])
        return jsonify({"success": True, "message": "Shutting down..."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/output/<filename>')
def serve_image(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == '__main__':
    ip = "localhost"
    try:
        output = subprocess.check_output(
            ["hostname", "-I"], text=True, timeout=5).strip()
        if output:
            ip = output.split()[0]
    except Exception:
        pass

    print("=" * 60)
    print("  Vignette - H System Smart Display")
    print(f"  Output directory: {OUTPUT_DIR}")
    print(f"  Local:   http://localhost:5000")
    print(f"  Network: http://{ip}:5000")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)
