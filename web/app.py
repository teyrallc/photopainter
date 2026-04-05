#!/usr/bin/env python3
"""
Vignette - H System Smart Display Web Control Interface
Flask web application for controlling the Waveshare 7.3" e-paper display.

Phase 2: Three page views (Home/Widget/Photo), Weather, Calendar,
         QR setup, photo rotation/fit, virtual buttons.
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

from flask import (Flask, jsonify, make_response, redirect, render_template,
                   request, send_file, send_from_directory, url_for)
from PIL import Image, ImageDraw, ImageFont

# Pillow compatibility
LANCZOS = getattr(Image, 'Resampling', Image).LANCZOS

# Project paths
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
LIB_DIR = os.path.join(PROJECT_DIR, "lib")
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")

# Add lib to path for waveshare_epd, add web to path for services
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, WEB_DIR)

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("vignette")

# Services
from services.config import Config
from services.weather import fetch_weather
from services.calendar_svc import fetch_calendar_events, get_today_info
from services.i18n import get_translations
from services import renderer

config = Config(CONFIG_PATH)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024


@app.context_processor
def inject_globals():
    """Inject translation strings and language into all templates."""
    lang = request.cookies.get("lang", config.get("lang", "en"))
    return {"t": get_translations(lang), "current_lang": lang}


@app.errorhandler(Exception)
def handle_exception(e):
    if request.path.startswith('/api/'):
        logger.error(f"API error: {e}", exc_info=True)
        code = getattr(e, 'code', 500)
        return jsonify({"error": str(e)}), code
    raise e


display_lock = threading.Lock()

# ── WiFi AP Hotspot Management ────────────────────────────────────────────

AP_SSID = "Vignette-Setup"
AP_PASSWORD = "vignette123"
AP_CONN_NAME = "Vignette-Hotspot"


def start_ap_hotspot():
    """Start WiFi Access Point so phones can connect and configure WiFi.
    Uses nmcli to create a hotspot on wlan0."""
    logger.info(f"Starting AP hotspot: {AP_SSID}")
    try:
        # Remove old hotspot connection if exists
        subprocess.run(
            ["nmcli", "connection", "delete", AP_CONN_NAME],
            capture_output=True, text=True, timeout=10)
        # Create and activate hotspot
        result = subprocess.run(
            ["nmcli", "device", "wifi", "hotspot",
             "ifname", "wlan0",
             "con-name", AP_CONN_NAME,
             "ssid", AP_SSID,
             "password", AP_PASSWORD],
            capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            logger.info(f"AP hotspot started: SSID={AP_SSID}, Pass={AP_PASSWORD}")
            return True
        else:
            logger.error(f"Failed to start hotspot: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Hotspot start error: {e}")
        return False


def stop_ap_hotspot():
    """Stop the WiFi AP hotspot."""
    logger.info("Stopping AP hotspot")
    try:
        subprocess.run(
            ["nmcli", "connection", "down", AP_CONN_NAME],
            capture_output=True, text=True, timeout=10)
        subprocess.run(
            ["nmcli", "connection", "delete", AP_CONN_NAME],
            capture_output=True, text=True, timeout=10)
        logger.info("AP hotspot stopped")
    except Exception as e:
        logger.error(f"Hotspot stop error: {e}")


def is_ap_active():
    """Check if the AP hotspot is currently running."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split('\n'):
            if AP_CONN_NAME in line:
                return True
    except Exception:
        pass
    return False

display_state = {
    "current_image": None,
    "last_update": None,
    "status": "idle",
}

photo_state = {
    "current_index": -1,
    "current_image": None,
    "total": 0,
}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'gif'}
EPD_WIDTH = 800
EPD_HEIGHT = 480

EPAPER_PALETTE = (
    0, 0, 0,        255, 255, 255,   255, 255, 0,
    255, 0, 0,      0, 0, 0,         0, 0, 255,
    0, 255, 0,
) + (0, 0, 0) * 249


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_image_list():
    images = []
    for ext in ALLOWED_EXTENSIONS:
        for f in Path(OUTPUT_DIR).glob(f"*.{ext}"):
            stat = f.stat()
            images.append({
                "filename": f.name, "path": str(f),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "modified_ts": stat.st_mtime,
            })
    images.sort(key=lambda x: x["modified_ts"], reverse=True)
    photo_state["total"] = len(images)
    return images


def get_current_photo_path():
    """Get the path of the current photo for display."""
    images = get_image_list()
    if not images:
        return None
    idx = photo_state["current_index"]
    if idx < 0 or idx >= len(images):
        idx = 0
    return os.path.join(OUTPUT_DIR, images[idx]["filename"])


def quantize_to_epaper(image_path):
    img = Image.open(image_path).convert("RGB")
    img = img.resize((EPD_WIDTH, EPD_HEIGHT), LANCZOS)
    pal_image = Image.new("P", (1, 1))
    pal_image.putpalette(EPAPER_PALETTE)
    img_quantized = img.quantize(palette=pal_image)
    img_rgb = img_quantized.convert("RGB")
    buf = io.BytesIO()
    img_rgb.save(buf, format='PNG')
    buf.seek(0)
    return buf


def process_upload(file_storage, rotation=0, fit_mode="fit"):
    """Process an uploaded image: save original, then create display-ready version."""
    from werkzeug.utils import secure_filename

    filename = secure_filename(file_storage.filename)
    base, ext = os.path.splitext(filename)
    if not base:
        base = f"upload_{int(time.time())}"
    if not ext:
        _, orig_ext = os.path.splitext(file_storage.filename)
        ext = orig_ext.lower() if orig_ext else ".png"
    filename = base + ext

    filepath = os.path.join(OUTPUT_DIR, filename)
    counter = 1
    while os.path.exists(filepath):
        filename = f"{base}_{counter}{ext}"
        filepath = os.path.join(OUTPUT_DIR, filename)
        counter += 1

    file_storage.save(filepath)

    # Apply rotation if requested
    img = Image.open(filepath).convert("RGB")
    if rotation:
        img = img.rotate(-rotation, expand=True)

    # Apply fit mode
    if fit_mode == "stretch":
        img = img.resize((EPD_WIDTH, EPD_HEIGHT), LANCZOS)
    else:
        # Fit: maintain aspect ratio, save at original (rotated) size
        img.thumbnail((EPD_WIDTH, EPD_HEIGHT), LANCZOS)
        # Create white canvas and center
        canvas = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), (255, 255, 255))
        px = (EPD_WIDTH - img.width) // 2
        py = (EPD_HEIGHT - img.height) // 2
        canvas.paste(img, (px, py))
        img = canvas

    img.save(filepath)
    return filename


# ── E-Paper Display Functions ──────────────────────────────────────────────

def display_pil_image(img):
    """Send a PIL Image to the e-paper display."""
    display_state["status"] = "displaying"
    logger.info("Sending image to e-paper...")
    try:
        from waveshare_epd import epd7in3e
        epd = epd7in3e.EPD()
        epd.init()
        buf = epd.getbuffer(img)
        epd.display(buf)
        epd.sleep()
        display_state["status"] = "idle"
        display_state["last_update"] = datetime.now().isoformat()
        logger.info("Display update complete!")
        return True, "OK"
    except Exception as e:
        logger.error(f"Display failed: {e}", exc_info=True)
        display_state["status"] = "error"
        return False, str(e)


def display_image_on_epaper(image_path):
    """Display an image file on e-paper."""
    display_state["current_image"] = os.path.basename(image_path)
    img = Image.open(image_path).convert("RGB")
    img = img.resize((EPD_WIDTH, EPD_HEIGHT), LANCZOS)
    return display_pil_image(img)


def display_current_page():
    """Render and display the current page view on e-paper."""
    page = config.get("current_page", "photo")
    logger.info(f"Rendering page: {page}")

    weather = None
    events = []
    if config.get("weather_api_key") and config.get("weather_city"):
        weather = fetch_weather(
            config.get("weather_api_key"),
            config.get("weather_city"),
            config.get("weather_units", "metric"),
            config.get("weather_lang", "en"))
    if config.get("calendar_ical_url"):
        events = fetch_calendar_events(config.get("calendar_ical_url"))

    photo_path = get_current_photo_path()

    if page == "home":
        img = renderer.render_home_page(weather, events, photo_path, config)
    elif page == "widget":
        mode = config.get("widget_mode", "weather")
        img = renderer.render_widget_page(mode, weather, events)
    else:  # photo
        rotation = config.get("photo_rotation", 0)
        fit_mode = config.get("photo_fit_mode", "fit")
        img = renderer.render_photo_page(photo_path, rotation, fit_mode)

    display_state["current_image"] = f"[{page} page]"
    return display_pil_image(img)


def display_qr_setup():
    """Display QR code setup page on e-paper."""
    ip = _get_ip()
    img = renderer.render_qr_setup(ip)
    display_state["current_image"] = "[QR setup]"
    return display_pil_image(img)


def display_test_pattern():
    """Send a test pattern to e-paper."""
    logger.info("Sending test pattern...")
    img = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    colors = [
        ((0, 0, 0), "Black"), ((255, 255, 255), "White"),
        ((0, 255, 0), "Green"), ((0, 0, 255), "Blue"),
        ((255, 0, 0), "Red"), ((255, 255, 0), "Yellow"),
    ]
    bar_width = EPD_WIDTH // len(colors)
    for i, (color, name) in enumerate(colors):
        x0, x1 = i * bar_width, (i + 1) * bar_width
        draw.rectangle([x0, 0, x1, EPD_HEIGHT], fill=color)
        tc = (255, 255, 255) if color in [(0, 0, 0), (0, 0, 255)] else (0, 0, 0)
        draw.text((x0 + 10, EPD_HEIGHT // 2), name, fill=tc)
    draw.rectangle([0, 0, EPD_WIDTH, 40], fill=(0, 0, 0))
    draw.text((10, 10), "Vignette - E-Paper Test Pattern", fill=(255, 255, 255))
    display_state["current_image"] = "[test pattern]"
    return display_pil_image(img)


# ── Photo Navigation ──────────────────────────────────────────────────────

def navigate_photo(direction):
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
    photo_state["current_image"] = images[idx]["filename"]
    photo_state["total"] = total

    # Use page rendering if on photo page, direct display otherwise
    if config.get("current_page") == "photo":
        filepath = os.path.join(OUTPUT_DIR, images[idx]["filename"])
        return display_image_on_epaper(filepath)
    else:
        return True, "Photo index updated (display page unchanged)"


# ── Page Routes ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if not config.is_setup_complete:
        return redirect(url_for('setup_page'))
    images = get_image_list()
    return render_template('index.html',
                           images=images[:5],
                           display_state=display_state,
                           photo_state=photo_state,
                           total_images=len(images),
                           config=config.to_dict())


@app.route('/setup')
def setup_page():
    return render_template('setup.html', config=config.to_dict())


@app.route('/upload')
def upload_page():
    return render_template('upload.html', config=config.to_dict())


@app.route('/gallery')
def gallery_page():
    images = get_image_list()
    return render_template('gallery.html', images=images, config=config.to_dict())


@app.route('/settings')
def settings_page():
    return render_template('settings.html', config=config.to_dict())


@app.route('/manual')
def manual_page():
    return render_template('manual.html')


@app.route('/wifi')
def wifi_page():
    return render_template('wifi.html', config=config.to_dict())


# ── Captive Portal Detection ─────────────────────────────────────────────
# When the Pi runs as a WiFi AP, phones probe these URLs to detect
# captive portals. Redirecting them sends the user straight to /wifi.

@app.route('/generate_204')
@app.route('/gen_204')
def captive_portal_android():
    """Android captive portal detection."""
    return redirect(url_for('wifi_page'))


@app.route('/hotspot-detect.html')
def captive_portal_apple():
    """Apple captive portal detection."""
    return redirect(url_for('wifi_page'))


@app.route('/connecttest.txt')
def captive_portal_windows():
    """Windows captive portal detection."""
    return redirect(url_for('wifi_page'))


@app.route('/ncsi.txt')
def captive_portal_windows_ncsi():
    """Windows NCSI captive portal detection."""
    return redirect(url_for('wifi_page'))


# ── API: Setup & Config ──────────────────────────────────────────────────

@app.route('/api/setup', methods=['POST'])
def api_setup():
    """Save initial setup configuration."""
    data = request.get_json() or {}
    config.update({
        "setup_complete": True,
        "weather_api_key": data.get("weather_api_key", ""),
        "weather_city": data.get("weather_city", ""),
        "weather_units": data.get("weather_units", "metric"),
        "calendar_ical_url": data.get("calendar_ical_url", ""),
        "current_page": data.get("current_page", "photo"),
    })
    logger.info("Setup complete!")
    return jsonify({"success": True, "message": "Setup saved"})


@app.route('/api/config', methods=['GET'])
def api_config_get():
    return jsonify(config.to_dict())


@app.route('/api/config', methods=['POST'])
def api_config_set():
    data = request.get_json() or {}
    config.update(data)
    return jsonify({"success": True, "config": config.to_dict()})


@app.route('/api/reset', methods=['POST'])
def api_reset():
    """Reset all settings to factory defaults (simulates first-time QR setup)."""
    config.reset()
    logger.info("System reset to factory defaults")
    # Start AP hotspot for WiFi configuration
    start_ap_hotspot()
    # Display QR setup on e-paper
    if display_lock.acquire(blocking=False):
        try:
            display_qr_setup()
        finally:
            display_lock.release()
    return jsonify({"success": True, "message": "Reset complete. QR setup displayed."})


# ── API: Page Control (virtual buttons) ──────────────────────────────────

@app.route('/api/page/switch', methods=['POST'])
def api_page_switch():
    """Switch between Home/Widget/Photo pages."""
    data = request.get_json() or {}
    target = data.get("page")
    pages = ["home", "widget", "photo"]

    if target:
        if target not in pages:
            return jsonify({"error": f"Invalid page: {target}"}), 400
        config.set("current_page", target)
    else:
        # Cycle: home → widget → photo → home
        current = config.get("current_page", "photo")
        idx = pages.index(current) if current in pages else 2
        config.set("current_page", pages[(idx + 1) % 3])

    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = display_current_page()
        page = config.get("current_page")
        if success:
            return jsonify({"success": True, "page": page})
        return jsonify({"error": msg}), 500
    finally:
        display_lock.release()


@app.route('/api/page/refresh', methods=['POST'])
def api_page_refresh():
    """Re-render and display the current page (refreshes weather/calendar data)."""
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = display_current_page()
        if success:
            return jsonify({"success": True, "page": config.get("current_page")})
        return jsonify({"error": msg}), 500
    finally:
        display_lock.release()


@app.route('/api/widget/toggle', methods=['POST'])
def api_widget_toggle():
    """Toggle widget between weather and calendar."""
    current = config.get("widget_mode", "weather")
    new_mode = "calendar" if current == "weather" else "weather"
    config.set("widget_mode", new_mode)

    if config.get("current_page") == "widget":
        if not display_lock.acquire(blocking=False):
            return jsonify({"error": "Display is busy"}), 503
        try:
            success, msg = display_current_page()
            if success:
                return jsonify({"success": True, "widget_mode": new_mode})
            return jsonify({"error": msg}), 500
        finally:
            display_lock.release()

    return jsonify({"success": True, "widget_mode": new_mode})


@app.route('/api/page/qr', methods=['POST'])
def api_page_qr():
    """Display QR setup code on e-paper."""
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = display_qr_setup()
        if success:
            return jsonify({"success": True, "message": "QR code displayed"})
        return jsonify({"error": msg}), 500
    finally:
        display_lock.release()


# ── API: Photo Settings ──────────────────────────────────────────────────

@app.route('/api/photo/rotation', methods=['POST'])
def api_photo_rotation():
    """Set photo rotation (0, 90, 180, 270)."""
    data = request.get_json() or {}
    rotation = data.get("rotation", 0)
    if rotation not in [0, 90, 180, 270]:
        return jsonify({"error": "Invalid rotation. Use 0, 90, 180, 270"}), 400
    config.set("photo_rotation", rotation)
    return jsonify({"success": True, "rotation": rotation})


@app.route('/api/photo/fit_mode', methods=['POST'])
def api_photo_fit_mode():
    """Set photo fit mode (fit or stretch)."""
    data = request.get_json() or {}
    mode = data.get("fit_mode", "fit")
    if mode not in ["fit", "stretch"]:
        return jsonify({"error": "Invalid mode. Use fit or stretch"}), 400
    config.set("photo_fit_mode", mode)
    return jsonify({"success": True, "fit_mode": mode})


# ── API: Image Management ─────────────────────────────────────────────────

@app.route('/api/upload', methods=['POST'])
def api_upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed"}), 400

    rotation = int(request.form.get('rotation', 0))
    fit_mode = request.form.get('fit_mode', config.get('photo_fit_mode', 'fit'))

    try:
        filename = process_upload(file, rotation, fit_mode)
        return jsonify({"success": True, "filename": filename,
                        "message": f"Image uploaded: {filename}"})
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        return jsonify({"error": f"Upload failed: {e}"}), 500


@app.route('/api/display', methods=['POST'])
def api_display():
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
            images = get_image_list()
            for i, img in enumerate(images):
                if img["filename"] == filename:
                    photo_state["current_index"] = i
                    photo_state["current_image"] = filename
                    break
            config.set("current_page", "photo")
            return jsonify({"success": True, "message": "Image displayed"})
        return jsonify({"error": f"Display failed: {msg}"}), 500
    finally:
        display_lock.release()


@app.route('/api/preview/<filename>')
def api_preview(filename):
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Image not found"}), 404
    buf = quantize_to_epaper(filepath)
    return send_file(buf, mimetype='image/png', download_name=f"preview_{filename}")


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


# ── API: Photo Navigation ───────────────────────────────────────────────

@app.route('/api/photo/current')
def api_photo_current():
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


# ── API: Language ────────────────────────────────────────────────────────

@app.route('/api/lang', methods=['POST'])
def api_lang():
    """Toggle language between en and zh."""
    data = request.get_json() or {}
    lang = data.get("lang", "en")
    if lang not in ("en", "zh"):
        lang = "en"
    config.set("lang", lang)
    resp = make_response(jsonify({"success": True, "lang": lang}))
    resp.set_cookie("lang", lang, max_age=365 * 24 * 3600)
    return resp


# ── API: Widget Mode Set ─────────────────────────────────────────────────

@app.route('/api/widget/set', methods=['POST'])
def api_widget_set():
    """Set widget mode to a specific value (weather or calendar)."""
    data = request.get_json() or {}
    mode = data.get("mode")
    if mode not in ("weather", "calendar"):
        return jsonify({"error": "Invalid mode. Use weather or calendar"}), 400
    config.set("widget_mode", mode)

    if config.get("current_page") == "widget":
        if not display_lock.acquire(blocking=False):
            return jsonify({"error": "Display is busy"}), 503
        try:
            success, msg = display_current_page()
            if success:
                return jsonify({"success": True, "widget_mode": mode})
            return jsonify({"error": msg}), 500
        finally:
            display_lock.release()

    return jsonify({"success": True, "widget_mode": mode})


# ── API: WiFi Management ─────────────────────────────────────────────────

@app.route('/api/wifi/status')
def api_wifi_status():
    """Get current WiFi connection status."""
    ap_active = is_ap_active()
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
            capture_output=True, text=True, timeout=10)
        for line in result.stdout.strip().split('\n'):
            parts = line.split(':')
            if len(parts) >= 3 and parts[0] == 'yes':
                return jsonify({"connected": True, "ssid": parts[1],
                                "signal": parts[2] + '%', "ip": _get_ip(),
                                "ap_active": ap_active})
        return jsonify({"connected": False, "ap_active": ap_active,
                        "ap_ssid": AP_SSID if ap_active else None})
    except Exception as e:
        logger.error(f"WiFi status error: {e}")
        return jsonify({"connected": False, "ap_active": ap_active, "error": str(e)})


@app.route('/api/wifi/scan')
def api_wifi_scan():
    """Scan for available WiFi networks."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=30)
        networks = []
        seen = set()
        for line in result.stdout.strip().split('\n'):
            parts = line.split(':')
            if len(parts) >= 3 and parts[0] and parts[0] not in seen:
                seen.add(parts[0])
                networks.append({"ssid": parts[0], "signal": parts[1] + '%',
                                 "security": parts[2]})
        networks.sort(key=lambda x: int(x["signal"].rstrip('%')), reverse=True)
        return jsonify({"networks": networks})
    except Exception as e:
        logger.error(f"WiFi scan error: {e}")
        return jsonify({"networks": [], "error": str(e)})


@app.route('/api/wifi/connect', methods=['POST'])
def api_wifi_connect():
    """Connect to a WiFi network. Stops AP hotspot first if active."""
    data = request.get_json() or {}
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()
    if not ssid:
        return jsonify({"error": "SSID is required"}), 400
    try:
        # Stop AP hotspot before connecting (can't do both on single wlan0)
        if is_ap_active():
            stop_ap_hotspot()
            time.sleep(2)  # Give wlan0 time to release AP mode

        cmd = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            logger.info(f"WiFi connected to {ssid}")
            return jsonify({"success": True, "message": f"Connected to {ssid}",
                            "ip": _get_ip()})
        else:
            # Connection failed - restart AP if we were in setup mode
            err = result.stderr.strip() or "Connection failed"
            logger.error(f"WiFi connect failed: {err}")
            if not config.is_setup_complete:
                start_ap_hotspot()
            return jsonify({"success": False, "error": err})
    except Exception as e:
        logger.error(f"WiFi connect error: {e}")
        if not config.is_setup_complete:
            start_ap_hotspot()
        return jsonify({"success": False, "error": str(e)})


# ── API: System ──────────────────────────────────────────────────────────

@app.route('/api/status')
def api_status():
    return jsonify({
        "display": display_state,
        "photo": photo_state,
        "config": config.to_dict(),
        "total_images": len(get_image_list()),
        "system": get_system_info(),
    })


@app.route('/api/weather')
def api_weather():
    """Get current weather data."""
    weather = fetch_weather(
        config.get("weather_api_key", ""),
        config.get("weather_city", ""),
        config.get("weather_units", "metric"),
        config.get("weather_lang", "zh_tw"))
    if weather:
        return jsonify(weather)
    return jsonify({"error": "No weather data. Check API key and city."}), 404


@app.route('/api/calendar')
def api_calendar():
    """Get upcoming calendar events."""
    events = fetch_calendar_events(config.get("calendar_ical_url", ""))
    today = get_today_info()
    return jsonify({"today": today, "events": [
        {"summary": e.get("summary"), "start": e["start"].isoformat() if e.get("start") else None}
        for e in events
    ]})


def _get_ip():
    try:
        return subprocess.check_output(
            ["hostname", "-I"], text=True, timeout=5).strip().split()[0]
    except Exception:
        return "localhost"


def get_system_info():
    info = {
        "hostname": "", "ip_addresses": [], "cpu_temp": None,
        "mem_total_mb": None, "mem_available_mb": None,
        "disk_free_gb": None, "uptime": None, "git_version": None,
    }
    try:
        info["hostname"] = subprocess.check_output(["hostname"], text=True, timeout=5).strip()
    except Exception:
        pass
    try:
        output = subprocess.check_output(["hostname", "-I"], text=True, timeout=5).strip()
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
    ip = _get_ip()
    print("=" * 60)
    print("  Vignette - H System Smart Display")
    print(f"  Output directory: {OUTPUT_DIR}")
    print(f"  Setup complete: {config.is_setup_complete}")
    print(f"  Local:   http://localhost:5000")
    print(f"  Network: http://{ip}:5000")
    print("=" * 60)

    # On first boot, start AP hotspot and display QR setup
    if not config.is_setup_complete:
        logger.info("First boot - starting AP hotspot and displaying QR setup")
        start_ap_hotspot()
        try:
            display_qr_setup()
        except Exception as e:
            logger.error(f"Could not display QR setup: {e}")

    app.run(host='0.0.0.0', port=5000, debug=False)
