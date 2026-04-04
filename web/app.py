#!/usr/bin/env python3
"""
PhotoPainter Web Control Interface
Flask web application for controlling the Waveshare 7.3" e-paper display.
"""

import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, url_for)
from PIL import Image

# Project paths
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
PROMPTS_DIR = os.path.join(PROJECT_DIR, "prompts")
LIB_DIR = os.path.join(PROJECT_DIR, "lib")
SRC_DIR = os.path.join(PROJECT_DIR, "src")

# Add lib to path for waveshare_epd
sys.path.insert(0, LIB_DIR)

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Display lock - only one display operation at a time
display_lock = threading.Lock()

# Generation state
generation_state = {
    "running": False,
    "progress": "",
    "prompt": "",
    "start_time": None,
    "error": None,
}

# Display state
display_state = {
    "current_image": None,
    "last_update": None,
    "status": "idle",  # idle, displaying, sleeping
}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'gif'}
EPD_WIDTH = 800
EPD_HEIGHT = 480

# 7-color palette for e-paper simulation
EPAPER_PALETTE = (
    0, 0, 0,        # Black
    255, 255, 255,   # White
    0, 255, 0,       # Green
    0, 0, 255,       # Blue
    255, 0, 0,       # Red
    255, 255, 0,     # Yellow
    255, 128, 0,     # Orange
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
    return images


def get_prompt_files():
    """Get list of available prompt files."""
    prompts = []
    for f in Path(PROMPTS_DIR).glob("*.json"):
        prompts.append(f.stem)
    return sorted(prompts)


def quantize_to_epaper(image_path):
    """Quantize an image to 7-color e-paper palette, return as PNG bytes."""
    img = Image.open(image_path).convert("RGB")

    # Resize to display dimensions
    img = img.resize((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)

    # Create palette image
    pal_image = Image.new("P", (1, 1))
    pal_image.putpalette(EPAPER_PALETTE)

    # Quantize with dithering
    img_quantized = img.quantize(palette=pal_image)
    img_rgb = img_quantized.convert("RGB")

    # Save to bytes
    buf = io.BytesIO()
    img_rgb.save(buf, format='PNG')
    buf.seek(0)
    return buf


def process_upload(file_storage):
    """Process an uploaded image: save original and create display-ready version."""
    from werkzeug.utils import secure_filename

    filename = secure_filename(file_storage.filename)
    if not filename:
        filename = f"upload_{int(time.time())}.png"

    # Ensure unique filename
    base, ext = os.path.splitext(filename)
    filepath = os.path.join(OUTPUT_DIR, filename)
    counter = 1
    while os.path.exists(filepath):
        filename = f"{base}_{counter}{ext}"
        filepath = os.path.join(OUTPUT_DIR, filename)
        counter += 1

    # Save original
    file_storage.save(filepath)

    # Create display-ready version (resized to 800x480)
    img = Image.open(filepath).convert("RGB")
    img_resized = img.resize((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)
    img_resized.save(filepath)

    return filename


def display_image_on_epaper(image_path):
    """Display image on e-paper using display_picture.py."""
    display_state["status"] = "displaying"
    display_state["current_image"] = os.path.basename(image_path)

    try:
        venv_python = os.path.join(PROJECT_DIR, "venv", "bin", "python")
        python = venv_python if os.path.exists(venv_python) else sys.executable
        display_script = os.path.join(SRC_DIR, "display_picture.py")

        result = subprocess.run(
            [python, display_script, "-r", image_path],
            capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            display_state["status"] = "error"
            return False, result.stderr
    except FileNotFoundError:
        # Hardware not available, try direct driver
        try:
            from waveshare_epd import epd7in3f
            epd = epd7in3f.EPD()
            epd.init()
            img = Image.open(image_path).convert("RGB")
            img = img.resize((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)
            buf = epd.getbuffer(img)
            epd.display(buf)
            epd.sleep()
        except Exception as e:
            display_state["status"] = "error"
            return False, str(e)
    except Exception as e:
        display_state["status"] = "error"
        return False, str(e)

    display_state["status"] = "idle"
    display_state["last_update"] = datetime.now().isoformat()
    return True, "OK"


def run_generation(prompt, prompt_file, seed, steps):
    """Run image generation in background thread."""
    generation_state["running"] = True
    generation_state["error"] = None
    generation_state["start_time"] = time.time()

    try:
        venv_python = os.path.join(PROJECT_DIR, "venv", "bin", "python")
        python = venv_python if os.path.exists(venv_python) else sys.executable
        gen_script = os.path.join(SRC_DIR, "generate_picture.py")

        cmd = [python, gen_script, OUTPUT_DIR, "--steps", str(steps)]

        if prompt:
            cmd.extend(["--prompt", prompt])
            generation_state["prompt"] = prompt
        elif prompt_file:
            cmd.extend(["--prompts", os.path.join(PROMPTS_DIR, f"{prompt_file}.json")])
            generation_state["prompt"] = f"[random from {prompt_file}]"

        if seed:
            cmd.extend(["--seed", str(seed)])

        generation_state["progress"] = "Starting generation..."

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600  # 1 hour timeout
        )

        if result.returncode == 0:
            generation_state["progress"] = "Complete!"
        else:
            generation_state["error"] = result.stderr or "Generation failed"
            generation_state["progress"] = "Failed"

    except subprocess.TimeoutExpired:
        generation_state["error"] = "Generation timed out (1 hour limit)"
        generation_state["progress"] = "Timed out"
    except Exception as e:
        generation_state["error"] = str(e)
        generation_state["progress"] = "Error"
    finally:
        generation_state["running"] = False


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Dashboard page."""
    images = get_image_list()
    return render_template('index.html',
                           images=images[:5],
                           display_state=display_state,
                           generation_state=generation_state,
                           total_images=len(images))


@app.route('/upload')
def upload_page():
    """Upload page."""
    return render_template('upload.html')


@app.route('/generate')
def generate_page():
    """AI generation page."""
    prompt_files = get_prompt_files()
    return render_template('generate.html',
                           prompt_files=prompt_files,
                           generation_state=generation_state)


@app.route('/gallery')
def gallery_page():
    """Image gallery page."""
    images = get_image_list()
    return render_template('gallery.html', images=images)


@app.route('/manual')
def manual_page():
    """Usage manual page."""
    return render_template('manual.html')


# ── API Routes ──────────────────────────────────────────────────────────────

@app.route('/api/upload', methods=['POST'])
def api_upload():
    """Handle image upload."""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed. Use: png, jpg, jpeg, bmp, gif"}), 400

    filename = process_upload(file)
    return jsonify({
        "success": True,
        "filename": filename,
        "message": f"Image uploaded: {filename}"
    })


@app.route('/api/display', methods=['POST'])
def api_display():
    """Display an image on e-paper."""
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
            return jsonify({"success": True, "message": "Image displayed"})
        else:
            return jsonify({"error": f"Display failed: {msg}"}), 500
    finally:
        display_lock.release()


@app.route('/api/preview/<filename>')
def api_preview(filename):
    """Return 7-color quantized preview of an image."""
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Image not found"}), 404

    buf = quantize_to_epaper(filepath)
    return send_file(buf, mimetype='image/png',
                     download_name=f"preview_{filename}")


@app.route('/api/generate', methods=['POST'])
def api_generate():
    """Start AI image generation."""
    if generation_state["running"]:
        return jsonify({"error": "Generation already in progress"}), 409

    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    prompt_file = data.get('prompt_file', 'flowers')
    seed = data.get('seed', '')
    steps = data.get('steps', 5)

    thread = threading.Thread(
        target=run_generation,
        args=(prompt, prompt_file, seed, steps),
        daemon=True
    )
    thread.start()

    return jsonify({
        "success": True,
        "message": "Generation started"
    })


@app.route('/api/generate/status')
def api_generate_status():
    """Get generation status."""
    elapsed = None
    if generation_state["start_time"]:
        elapsed = int(time.time() - generation_state["start_time"])

    return jsonify({
        "running": generation_state["running"],
        "progress": generation_state["progress"],
        "prompt": generation_state["prompt"],
        "elapsed_seconds": elapsed,
        "error": generation_state["error"],
    })


@app.route('/api/images')
def api_images():
    """List all images."""
    return jsonify(get_image_list())


@app.route('/api/images/<filename>', methods=['DELETE'])
def api_delete_image(filename):
    """Delete an image."""
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Image not found"}), 404

    os.remove(filepath)
    return jsonify({"success": True, "message": f"Deleted {filename}"})


@app.route('/api/clear', methods=['POST'])
def api_clear():
    """Clear the e-paper display."""
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503

    try:
        from waveshare_epd import epd7in3f
        epd = epd7in3f.EPD()
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
    """Put display to sleep."""
    try:
        from waveshare_epd import epd7in3f
        epd = epd7in3f.EPD()
        epd.init()
        epd.sleep()
        display_state["status"] = "sleeping"
        return jsonify({"success": True, "message": "Display sleeping"})
    except Exception as e:
        return jsonify({"error": f"Sleep failed: {e}"}), 500


@app.route('/api/status')
def api_status():
    """Get system status."""
    return jsonify({
        "display": display_state,
        "generation": {
            "running": generation_state["running"],
            "prompt": generation_state["prompt"],
        },
        "total_images": len(get_image_list()),
        "system": get_system_info(),
    })


# ── System / Remote Management API ─────────────────────────────────────────

def get_system_info():
    """Collect Raspberry Pi system information."""
    info = {
        "hostname": "",
        "ip_addresses": [],
        "cpu_temp": None,
        "mem_total_mb": None,
        "mem_available_mb": None,
        "disk_free_gb": None,
        "uptime": None,
        "git_version": None,
    }

    try:
        info["hostname"] = subprocess.check_output(
            ["hostname"], text=True, timeout=5
        ).strip()
    except Exception:
        pass

    try:
        output = subprocess.check_output(
            ["hostname", "-I"], text=True, timeout=5
        ).strip()
        info["ip_addresses"] = output.split()
    except Exception:
        pass

    # CPU temperature
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            info["cpu_temp"] = round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        pass

    # Memory
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    info["mem_total_mb"] = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    info["mem_available_mb"] = int(line.split()[1]) // 1024
    except Exception:
        pass

    # Disk
    try:
        stat = os.statvfs(PROJECT_DIR)
        info["disk_free_gb"] = round((stat.f_bavail * stat.f_frsize) / (1024**3), 1)
    except Exception:
        pass

    # Uptime
    try:
        with open("/proc/uptime") as f:
            uptime_secs = int(float(f.read().split()[0]))
            hours, remainder = divmod(uptime_secs, 3600)
            minutes, secs = divmod(remainder, 60)
            info["uptime"] = f"{hours}h {minutes}m {secs}s"
    except Exception:
        pass

    # Git version
    try:
        info["git_version"] = subprocess.check_output(
            ["git", "-C", PROJECT_DIR, "log", "--oneline", "-1"],
            text=True, timeout=5
        ).strip()
    except Exception:
        pass

    return info


@app.route('/api/system/info')
def api_system_info():
    """Get detailed system information."""
    return jsonify(get_system_info())


@app.route('/api/system/update', methods=['POST'])
def api_system_update():
    """Pull latest code from git and restart service."""
    update_script = os.path.join(PROJECT_DIR, "scripts", "update.sh")
    if not os.path.exists(update_script):
        return jsonify({"error": "Update script not found"}), 500

    try:
        result = subprocess.run(
            ["bash", update_script],
            capture_output=True, text=True, timeout=300,
            cwd=PROJECT_DIR
        )
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
    """Reboot the Raspberry Pi."""
    try:
        subprocess.Popen(["sudo", "reboot"])
        return jsonify({"success": True, "message": "Rebooting..."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/system/shutdown', methods=['POST'])
def api_system_shutdown():
    """Shutdown the Raspberry Pi."""
    try:
        subprocess.Popen(["sudo", "shutdown", "-h", "now"])
        return jsonify({"success": True, "message": "Shutting down..."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/output/<filename>')
def serve_image(filename):
    """Serve images from output directory."""
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == '__main__':
    # Get local IP for display
    ip = "localhost"
    try:
        output = subprocess.check_output(["hostname", "-I"], text=True, timeout=5).strip()
        if output:
            ip = output.split()[0]
    except Exception:
        pass

    print("=" * 60)
    print("  PhotoPainter Web Control Interface")
    print(f"  Output directory: {OUTPUT_DIR}")
    print(f"  Local access:  http://localhost:5000")
    print(f"  Network access: http://{ip}:5000")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)
