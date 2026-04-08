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
                   request, send_file, send_from_directory, session, url_for)
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
from services import gdrive

config = Config(CONFIG_PATH)

app = Flask(__name__)
app.secret_key = config.get("session_secret", os.urandom(24).hex())
if not config.get("session_secret"):
    config.set("session_secret", app.secret_key)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# ── Auth System ────────────────────────────────────────────────────────
from services import auth_mgr
auth_mgr.set_config_ref(config)

from auth import bp as auth_bp
app.register_blueprint(auth_bp)

@app.before_request
def enforce_auth():
    """Globally protect routes. Exceptions are static files, wifi setup forms, and the auth blueprint itself."""
    # Allow unauthenticated access to setup, static, auth pages, and wifi connection/status
    if request.path.startswith('/static') or request.path.startswith('/auth'):
        return
    if request.path.startswith('/api/wifi/'):
        return
    if request.path.startswith('/output/'):
        return

    # If AP mode is active (setup_complete is False), we let people use the local network setup portal
    if not config.is_setup_complete:
        return

    if not config.get("admin_email"):
        if request.path.startswith('/api/'):
            return jsonify({"error": "Setup required."}), 401
        return redirect(url_for('auth.setup_admin'))
        
    if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({"error": "Unauthorized.", "redirect": "/auth/login"}), 401
        return redirect(url_for('auth.login', next=request.url))


@app.context_processor
def inject_globals():
    """Inject translation strings and language into all templates."""
    lang = request.cookies.get("lang", config.get("lang", "en"))
    return {"t": get_translations(lang), "current_lang": lang}


@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled error on {request.path}: {e}", exc_info=True)
    if request.path.startswith('/api/') or request.path.startswith('/auth/'):
        code = getattr(e, 'code', 500)
        return jsonify({"error": str(e)}), code
    raise e


display_lock = threading.RLock()

# ── WiFi AP Hotspot Management ────────────────────────────────────────────

AP_SSID = "Vignette-Setup"
AP_PASSWORD = "vignette123"
AP_CONN_NAME = "Vignette-Hotspot"

# Cached WiFi scan results (scanned before AP starts)
_cached_wifi_networks = []


def scan_and_cache_wifi():
    """Scan WiFi networks BEFORE starting AP. Cache results for the setup page.
    Must be called while wlan0 is in station mode (not AP)."""
    global _cached_wifi_networks
    try:
        logger.info("Scanning WiFi networks before starting AP...")
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
        _cached_wifi_networks = networks
        logger.info(f"Cached {len(networks)} WiFi networks")
    except Exception as e:
        logger.error(f"WiFi pre-scan error: {e}")
        _cached_wifi_networks = []


def start_ap_hotspot():
    """Start WiFi Access Point so phones can connect and configure WiFi.
    Uses nmcli to create a hotspot on wlan0. No captive portal — user
    manually opens http://192.168.4.1:5000 in their browser."""
    logger.info(f"Starting AP hotspot: {AP_SSID}")
    try:
        # Remove old hotspot connection if exists
        subprocess.run(
            ["nmcli", "connection", "delete", AP_CONN_NAME],
            capture_output=True, text=True, timeout=10)

        # Create connection profile with explicit settings
        result = subprocess.run([
            "nmcli", "connection", "add",
            "type", "wifi",
            "ifname", "wlan0",
            "con-name", AP_CONN_NAME,
            "autoconnect", "no",
            "ssid", AP_SSID,
            "mode", "ap",
            "ipv4.method", "shared",
            "ipv4.addresses", "192.168.4.1/24",
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", AP_PASSWORD,
        ], capture_output=True, text=True, timeout=15)

        if result.returncode != 0:
            logger.error(f"Failed to create hotspot profile: {result.stderr}")
            return False

        # Activate the connection
        result = subprocess.run(
            ["nmcli", "connection", "up", AP_CONN_NAME],
            capture_output=True, text=True, timeout=15)

        if result.returncode == 0:
            logger.info(f"AP hotspot started: SSID={AP_SSID}, Pass={AP_PASSWORD}")
            return True
        else:
            logger.error(f"Failed to activate hotspot: {result.stderr}")
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
    out = Path(OUTPUT_DIR)
    for f in out.iterdir():
        if f.is_file() and f.suffix.lower().lstrip('.') in ALLOWED_EXTENSIONS:
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
    """Send a PIL Image to the e-paper display with thread safety and retry logic."""
    with display_lock:
        display_state["status"] = "displaying"
        logger.info("Sending image to e-paper...")
        
        # Try up to 3 times to initialize and display
        last_error = None
        for attempt in range(3):
            try:
                from waveshare_epd import epd7in3e
                epd = epd7in3e.EPD()
                
                # Hardware init
                epd.init()
                
                # Render and show
                buf = epd.getbuffer(img)
                epd.display(buf)
                
                # Power off
                epd.sleep()
                
                display_state["status"] = "idle"
                display_state["last_update"] = datetime.now().isoformat()
                logger.info(f"Display update complete! (Attempt {attempt + 1})")
                return True, "OK"
            except Exception as e:
                last_error = e
                logger.warning(f"Display attempt {attempt + 1} failed: {e}")
                time.sleep(1) # Wait a bit before retry
                
        logger.error(f"Display failed after 3 attempts: {last_error}", exc_info=True)
        display_state["status"] = "error"
        return False, str(last_error)


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


def display_wifi_connected(ssid, ip_address):
    """Display 'WiFi Connected' confirmation on e-paper with new IP."""
    img = renderer.render_wifi_connected(ssid, ip_address)
    display_state["current_image"] = "[WiFi connected]"
    return display_pil_image(img)


def display_otp_code(code):
    """Display 6-digit Hardware Auth OTP on e-paper."""
    img = renderer.render_otp_page(code)
    display_state["current_image"] = "[OTP Code]"
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
    """Navigate photos (config-only, no e-paper update)."""
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
    return True, "Photo index updated"


# ── Page Routes ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if not config.is_setup_complete:
        return render_template('wifi_setup.html')
    images = get_image_list()
    return render_template('index.html',
                           images=images[:5],
                           display_state=display_state,
                           photo_state=photo_state,
                           total_images=len(images),
                           config=config.to_dict(),
                           now=int(time.time()))


@app.route('/setup')
def setup_page():
    if not config.is_setup_complete:
        return render_template('wifi_setup.html')
    return render_template('setup.html', config=config.to_dict())



@app.route('/upload')
def upload_page():
    return render_template('upload.html', config=config.to_dict())


@app.route('/gallery')
def gallery_page():
    images = get_image_list()
    gdrive_connected = config.get("gdrive_connected", False)
    gdrive_configured = bool(config.get("gdrive_client_id", ""))
    return render_template('gallery.html', images=images, config=config.to_dict(),
                           gdrive_connected=gdrive_connected,
                           gdrive_configured=gdrive_configured)


@app.route('/settings')
def settings_page():
    return render_template('settings.html', config=config.to_dict())


@app.route('/manual')
def manual_page():
    return render_template('manual.html')


@app.route('/wifi')
def wifi_page():
    return render_template('wifi.html', config=config.to_dict())


@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Not found"}), 404


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
    display_qr_setup()
    return jsonify({"success": True, "message": "Reset complete. QR setup displayed."})


# ── API: Page Control (virtual buttons) ──────────────────────────────────

@app.route('/api/page/switch', methods=['POST'])
def api_page_switch():
    """Switch between Home/Widget/Photo pages (config only, no e-paper update)."""
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

    page = config.get("current_page")
    return jsonify({"success": True, "page": page})


@app.route('/api/page/refresh', methods=['POST'])
def api_page_refresh():
    """Re-render and display the current page (refreshes weather/calendar data)."""
    # Check if busy without blocking forever
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
    """Toggle widget between weather and calendar (config only)."""
    current = config.get("widget_mode", "weather")
    new_mode = "calendar" if current == "weather" else "weather"
    config.set("widget_mode", new_mode)
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


@app.route('/api/upload/batch', methods=['POST'])
def api_upload_batch():
    """Upload multiple images at once."""
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "No files provided"}), 400

    rotation = int(request.form.get('rotation', 0))
    fit_mode = request.form.get('fit_mode', config.get('photo_fit_mode', 'fit'))

    results = []
    for file in files:
        if file.filename == '' or not allowed_file(file.filename):
            results.append({"filename": file.filename, "success": False,
                            "error": "Invalid file"})
            continue
        try:
            filename = process_upload(file, rotation, fit_mode)
            results.append({"filename": filename, "success": True})
        except Exception as e:
            results.append({"filename": file.filename, "success": False,
                            "error": str(e)})

    ok = sum(1 for r in results if r["success"])
    return jsonify({"success": True, "uploaded": ok,
                    "total": len(results), "results": results})


# ── API: Google Drive ─────────────────────────────────────────────────────

def _gdrive_redirect_uri():
    """Build the OAuth redirect URI based on current request host."""
    return f"http://{request.host}/api/gdrive/callback"


def _gdrive_access_token():
    """Get a valid access token, refreshing if needed."""
    token = config.get("gdrive_access_token")
    if token:
        return token
    refresh = config.get("gdrive_refresh_token")
    if not refresh:
        return None
    new_token = gdrive.refresh_access_token(
        config.get("gdrive_client_id", ""),
        config.get("gdrive_client_secret", ""),
        refresh)
    if new_token:
        config.set("gdrive_access_token", new_token)
    return new_token


@app.route('/api/gdrive/config')
def api_gdrive_config():
    """Return Google Drive client ID for the GIS popup flow."""
    client_id = config.get("gdrive_client_id", "")
    return jsonify({
        "client_id": client_id,
        "connected": config.get("gdrive_connected", False),
    })


@app.route('/api/gdrive/auth', methods=['POST'])
def api_gdrive_auth():
    """Exchange auth code from Google Sign-In popup for tokens."""
    data = request.get_json() or {}
    code = data.get("code")
    if not code:
        return jsonify({"error": "No auth code"}), 400

    # GIS popup flow uses 'postmessage' as redirect_uri for token exchange
    tokens = gdrive.exchange_code(
        config.get("gdrive_client_id", ""),
        config.get("gdrive_client_secret", ""),
        code, "postmessage")

    if not tokens:
        return jsonify({"error": "Token exchange failed"}), 400

    config.update({
        "gdrive_access_token": tokens.get("access_token", ""),
        "gdrive_refresh_token": tokens.get("refresh_token",
                                           config.get("gdrive_refresh_token", "")),
        "gdrive_connected": True,
    })
    return jsonify({"success": True})


@app.route('/api/gdrive/callback')
def api_gdrive_callback():
    """OAuth redirect callback (used by GIS code flow as redirect_uri)."""
    code = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        return redirect(url_for('gallery_page'))

    tokens = gdrive.exchange_code(
        config.get("gdrive_client_id", ""),
        config.get("gdrive_client_secret", ""),
        code, _gdrive_redirect_uri())

    if tokens:
        config.update({
            "gdrive_access_token": tokens.get("access_token", ""),
            "gdrive_refresh_token": tokens.get("refresh_token",
                                               config.get("gdrive_refresh_token", "")),
            "gdrive_connected": True,
        })
    return redirect(url_for('gallery_page'))


@app.route('/api/gdrive/disconnect', methods=['POST'])
def api_gdrive_disconnect():
    """Disconnect Google Drive."""
    config.update({
        "gdrive_access_token": "",
        "gdrive_refresh_token": "",
        "gdrive_connected": False,
    })
    return jsonify({"success": True})


@app.route('/api/gdrive/files')
def api_gdrive_files():
    """List image files from Google Drive."""
    token = _gdrive_access_token()
    if not token:
        return jsonify({"error": "Not connected to Google Drive"}), 401
    page_token = request.args.get("pageToken")
    result = gdrive.list_images(token, page_token)
    if "error" in result:
        # Token might be expired, clear it so next call refreshes
        config.set("gdrive_access_token", "")
        return jsonify({"error": result["error"]}), 401
    return jsonify(result)


@app.route('/api/gdrive/download', methods=['POST'])
def api_gdrive_download():
    """Download selected files from Google Drive to local output directory."""
    token = _gdrive_access_token()
    if not token:
        return jsonify({"error": "Not connected to Google Drive"}), 401

    data = request.get_json() or {}
    files = data.get("files", [])  # list of {id, name}
    if not files:
        return jsonify({"error": "No files selected"}), 400

    rotation = int(data.get("rotation", 0))
    fit_mode = data.get("fit_mode", config.get("photo_fit_mode", "fit"))

    results = []
    for f in files:
        file_id = f.get("id")
        name = f.get("name", f"{file_id}.jpg")
        if not file_id:
            continue

        # Sanitize filename
        from werkzeug.utils import secure_filename
        safe_name = secure_filename(name)
        if not safe_name:
            safe_name = f"gdrive_{file_id}.jpg"

        dest = os.path.join(OUTPUT_DIR, safe_name)
        counter = 1
        base, ext = os.path.splitext(safe_name)
        while os.path.exists(dest):
            safe_name = f"{base}_{counter}{ext}"
            dest = os.path.join(OUTPUT_DIR, safe_name)
            counter += 1

        ok = gdrive.download_file(token, file_id, dest)
        if ok:
            # Process the downloaded image (rotation + fit)
            try:
                img = Image.open(dest).convert("RGB")
                if rotation:
                    img = img.rotate(-rotation, expand=True)
                if fit_mode == "stretch":
                    img = img.resize((EPD_WIDTH, EPD_HEIGHT), LANCZOS)
                else:
                    img.thumbnail((EPD_WIDTH, EPD_HEIGHT), LANCZOS)
                    canvas = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), (255, 255, 255))
                    px = (EPD_WIDTH - img.width) // 2
                    py = (EPD_HEIGHT - img.height) // 2
                    canvas.paste(img, (px, py))
                    img = canvas
                img.save(dest)
                results.append({"name": safe_name, "success": True})
            except Exception as e:
                logger.error(f"Failed to process {safe_name}: {e}")
                results.append({"name": safe_name, "success": False, "error": str(e)})
        else:
            results.append({"name": name, "success": False, "error": "Download failed"})

    ok_count = sum(1 for r in results if r["success"])
    return jsonify({"success": True, "downloaded": ok_count,
                    "total": len(results), "results": results})


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
    success, msg = navigate_photo("next")
    if success:
        return jsonify({"success": True, "photo": photo_state})
    return jsonify({"error": msg}), 500


@app.route('/api/photo/prev', methods=['POST'])
def api_photo_prev():
    success, msg = navigate_photo("prev")
    if success:
        return jsonify({"success": True, "photo": photo_state})
    return jsonify({"error": msg}), 500


@app.route('/api/photo/latest', methods=['POST'])
def api_photo_latest():
    success, msg = navigate_photo("latest")
    if success:
        return jsonify({"success": True, "photo": photo_state})
    return jsonify({"error": msg}), 500


@app.route('/api/photo/goto/<int:idx>', methods=['POST'])
def api_photo_goto(idx):
    success, msg = navigate_photo(idx)
    if success:
        return jsonify({"success": True, "photo": photo_state})
    return jsonify({"error": msg}), 500


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


# ── API: E-paper Preview (for dashboard) ──────────────────────────────────

@app.route('/api/preview/current')
def api_preview_current():
    """Render the current page as a PNG for the dashboard preview."""
    page = config.get("current_page", "photo")
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
    else:
        rotation = config.get("photo_rotation", 0)
        fit_mode = config.get("photo_fit_mode", "fit")
        img = renderer.render_photo_page(photo_path, rotation, fit_mode)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name='preview.png')


# ── API: Slideshow ──────────────────────────────────────────────────────

slideshow_thread = None
_slideshow_stop = threading.Event()


def _slideshow_loop():
    """Background thread: cycle through selected photos at interval."""
    logger.info("Slideshow started")
    while not _slideshow_stop.is_set():
        interval = config.get("slideshow_interval", 300)
        _slideshow_stop.wait(interval)
        if _slideshow_stop.is_set():
            break

        selected = config.get("slideshow_photos", [])
        order = config.get("slideshow_order", "sequential")
        images = get_image_list()
        if not images:
            continue

        # Filter to selected photos (empty = all)
        if selected:
            pool = [img for img in images if img["filename"] in selected]
        else:
            pool = images
        if not pool:
            continue

        idx = photo_state["current_index"]
        if order == "random":
            import random
            new_idx_in_pool = random.randint(0, len(pool) - 1)
        else:
            # Find current position in pool
            current_name = photo_state.get("current_image")
            cur_pool_idx = -1
            for i, p in enumerate(pool):
                if p["filename"] == current_name:
                    cur_pool_idx = i
                    break
            new_idx_in_pool = (cur_pool_idx + 1) % len(pool)

        target_name = pool[new_idx_in_pool]["filename"]
        # Find in full image list to set photo_state
        for i, img in enumerate(images):
            if img["filename"] == target_name:
                photo_state["current_index"] = i
                photo_state["current_image"] = target_name
                photo_state["total"] = len(images)
                break

        # Update e-paper
        if display_lock.acquire(blocking=False):
            try:
                display_current_page()
            except Exception as e:
                logger.error(f"Slideshow update failed: {e}")
            finally:
                display_lock.release()

    logger.info("Slideshow stopped")


@app.route('/api/slideshow/start', methods=['POST'])
def api_slideshow_start():
    """Start photo slideshow."""
    global slideshow_thread
    data = request.get_json() or {}

    # Save slideshow config
    if "photos" in data:
        config.set("slideshow_photos", data["photos"])  # list of filenames, [] = all
    if "interval" in data:
        config.set("slideshow_interval", max(int(data["interval"]), 30))  # min 30s
    if "order" in data:
        config.set("slideshow_order", data["order"])  # "sequential" or "random"

    config.set("slideshow_active", True)

    # Stop existing if running
    if slideshow_thread and slideshow_thread.is_alive():
        _slideshow_stop.set()
        slideshow_thread.join(timeout=5)

    _slideshow_stop.clear()
    slideshow_thread = threading.Thread(target=_slideshow_loop, daemon=True)
    slideshow_thread.start()

    return jsonify({"success": True, "message": "Slideshow started"})


@app.route('/api/slideshow/stop', methods=['POST'])
def api_slideshow_stop():
    """Stop photo slideshow."""
    global slideshow_thread
    _slideshow_stop.set()
    config.set("slideshow_active", False)
    if slideshow_thread and slideshow_thread.is_alive():
        slideshow_thread.join(timeout=5)
    slideshow_thread = None
    return jsonify({"success": True, "message": "Slideshow stopped"})


@app.route('/api/slideshow/status')
def api_slideshow_status():
    return jsonify({
        "active": config.get("slideshow_active", False),
        "photos": config.get("slideshow_photos", []),
        "interval": config.get("slideshow_interval", 300),
        "order": config.get("slideshow_order", "sequential"),
    })


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
    """Set widget mode (config only, no e-paper update)."""
    data = request.get_json() or {}
    mode = data.get("mode")
    if mode not in ("weather", "calendar", "split"):
        return jsonify({"error": "Invalid mode. Use weather, calendar, or split"}), 400
    config.set("widget_mode", mode)
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
    """Return available WiFi networks. In AP mode, returns pre-cached results
    (scanned before AP started). Otherwise does a live scan."""
    if is_ap_active():
        return jsonify({"networks": _cached_wifi_networks, "cached": True})
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
        return jsonify({"networks": [], "error": str(e)})


# Background WiFi connection state (shared between request handler and bg thread)
_wifi_connect_state = {
    "status": "idle",       # idle | connecting | success | failed
    "ssid": "",
    "ip": None,
    "error": None,
}


def _background_wifi_connect(ssid, password):
    """Run WiFi connection in a background thread.
    This is necessary because stopping the AP disconnects the client (phone),
    which would abort the HTTP request before WiFi connect is even attempted."""
    global _wifi_connect_state
    _wifi_connect_state.update({
        "status": "connecting", "ssid": ssid, "ip": None, "error": None, "redirect_url": None})

    try:
        # Step 1: Stop the AP hotspot gracefully (this dismantles the NM iptables NAT correctly)
        logger.info("Background WiFi: Stopping AP hotspot gracefully")
        stop_ap_hotspot()

        # Step 2: Wait for interface to fully release
        time.sleep(3)

        # Step 3: Connect to the target WiFi using non-interactive method
        # Using 'nmcli connection add' + 'nmcli connection up' avoids the
        # interactive agent authentication prompt that blocks headless devices.
        logger.info(f"Background WiFi: Connecting to {ssid}")
        
        # Remove any existing connection for this SSID (prevents bad saved password issues)
        subprocess.run(["nmcli", "connection", "delete", ssid],
                       capture_output=True, text=True, timeout=5)

        # Build the connection profile non-interactively
        add_cmd = [
            "nmcli", "connection", "add",
            "type", "wifi",
            "ifname", "wlan0",
            "con-name", ssid,
            "ssid", ssid,
        ]
        if password:
            add_cmd += [
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", password,
            ]
        
        add_result = subprocess.run(add_cmd, capture_output=True, text=True, timeout=10)
        logger.info(f"Background WiFi: nmcli add result: {add_result.stdout.strip()} {add_result.stderr.strip()}")

        # Activate the connection (non-interactive, no agent needed)
        try:
            result = subprocess.run(
                ["nmcli", "connection", "up", ssid],
                capture_output=True, text=True, timeout=30)
            logger.info(f"Background WiFi: nmcli up result: {result.stdout.strip()} {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            logger.warning("Background WiFi: nmcli timed out, checking connection status")
            result = None

        # Step 4: Verify connection (nmcli can return failure/timeout but still connect)
        is_connected = False
        for _ in range(5):  # Poll up to 10 seconds
            check = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"], 
                                   capture_output=True, text=True, timeout=5)
            if any(line.startswith(f"yes:{ssid}") for line in check.stdout.strip().split('\n')):
                is_connected = True
                break
            time.sleep(2)

        if is_connected:
            logger.info(f"Background WiFi: Successfully connected to {ssid}")

            # Ensure NM saves this connection with autoconnect and high priority
            subprocess.run(
                ["nmcli", "connection", "modify", ssid,
                 "connection.autoconnect", "yes",
                 "connection.autoconnect-priority", "100"],
                capture_output=True, text=True, timeout=5)

            # Double-check AP profile is gone
            subprocess.run(
                ["nmcli", "connection", "delete", AP_CONN_NAME],
                capture_output=True, text=True, timeout=5)

            new_ip = _get_ip()
            logger.info(f"Background WiFi: New IP = {new_ip}")

            # Mark setup complete upon ACTUAL success
            config.set("setup_complete", True)

            # Start ngrok tunnel and get the public URL
            redirect_url = f"http://{new_ip}:5000"
            try:
                from pyngrok import ngrok
                logger.info("Initializing ngrok tunnel after setup...")
                ngrok.set_auth_token("3By64a7MxTOJAWF3eD8TGoLcaIl_5tprPKw4ftjjRSTWU1eVM")
                public_url = ngrok.connect(5000).public_url
                logger.info(f"Ngrok Tunnel Active: {public_url}")
                redirect_url = public_url
                real_ssid = get_active_ssid() or ssid
                display_wifi_connected(real_ssid, public_url.replace("https://", ""))
            except Exception as e:
                logger.error(f"Failed to start ngrok in background: {e}")
                try:
                    real_ssid = get_active_ssid() or ssid
                    display_wifi_connected(real_ssid, new_ip)
                except Exception:
                    pass

            _wifi_connect_state.update({
                "status": "success", "ip": new_ip, "redirect_url": redirect_url})
        else:
            err = "Timeout" if result is None else (result.stderr.strip() or "Connection failed")
            logger.error(f"Background WiFi: Connect failed: {err}")
            _wifi_connect_state.update({"status": "failed", "error": err})

            # Revert: restart AP so user can try again
            config.set("setup_complete", False)
            scan_and_cache_wifi()
            start_ap_hotspot()
            logger.info("Background WiFi: AP hotspot restarted for retry")

    except Exception as e:
        logger.error(f"Background WiFi: Error: {e}")
        _wifi_connect_state.update({"status": "failed", "error": str(e)})
        config.set("setup_complete", False)
        scan_and_cache_wifi()
        start_ap_hotspot()
        logger.info("Background WiFi: AP hotspot restarted after error")


@app.route('/api/wifi/connect', methods=['POST'])
def api_wifi_connect():
    """Accept WiFi credentials and start connection in background.
    Responds immediately so the client gets the response BEFORE the AP stops.
    The actual AP-stop → WiFi-connect → fallback runs in a background thread."""
    data = request.get_json() or {}
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()
    if not ssid:
        return jsonify({"error": "SSID is required"}), 400

    if _wifi_connect_state["status"] == "connecting":
        return jsonify({"error": "Connection already in progress"}), 409

    # Record the credentials in the document BEFORE starting the thread
    config.set("wifi_ssid", ssid)
    if password:
        config.set("wifi_password", password)
    else:
        config.set("wifi_password", "")
    
    # We DO NOT set setup_complete=True here anymore. 
    # It will only be set to True if the background thread succeeds in connecting.

    # Start background thread — respond to client FIRST, then stop AP
    thread = threading.Thread(
        target=_background_wifi_connect, args=(ssid, password), daemon=True)
    thread.start()

    # Get hostname for mDNS auto-redirect (client will try http://hostname.local:5000)
    try:
        hostname = subprocess.check_output(["hostname"], text=True, timeout=5).strip()
    except Exception:
        hostname = "vignette"

    # Return immediately while client is still connected to AP
    return jsonify({"success": True, "message": "Connection attempt started",
                    "status": "connecting", "hostname": hostname})


@app.route('/api/wifi/connect/status')
def api_wifi_connect_status():
    """Poll the result of a background WiFi connection attempt.
    Needs CORS because the client page was loaded from 192.168.4.1 (AP)
    but polls this endpoint on hostname.local (new network)."""
    resp = jsonify(_wifi_connect_state)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET'
    return resp


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
        config.get("weather_lang", "en"))
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
        ips = subprocess.check_output(
            ["hostname", "-I"], text=True, timeout=5).strip().split()
        for ip in ips:
            if ip != "192.168.4.1":
                return ip
        return ips[0] if ips else "localhost"
    except Exception:
        return "localhost"


def get_active_ssid():
    """Dynamically get the currently active WiFi from NetworkManager."""
    try:
        check = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"], 
                               capture_output=True, text=True, timeout=5)
        for line in check.stdout.strip().split('\n'):
            if line.startswith("yes:"):
                return line.split(':', 1)[1].strip()
    except Exception:
        pass
    return None

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


def verify_or_connect_wifi_on_boot():
    ssid = config.get("wifi_ssid", "")
    password = config.get("wifi_password", "")
    
    if not ssid:
        return False

    logger.info(f"Boot check: Verifying connection to {ssid}...")
    
    # 1. Wait up to 15 seconds for NM auto-connect
    for _ in range(15):
        try:
            check = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"], 
                                   capture_output=True, text=True, timeout=5)
            if any(line.startswith(f"yes:{ssid}") for line in check.stdout.strip().split('\n')):
                logger.info(f"Boot check: Auto-connected to {ssid} successfully.")
                return True
        except Exception as e:
            pass
        time.sleep(1)
        
    # 2. Try a manual connection using the document record
    logger.warning(f"Boot check: Auto-connect timed out for {ssid}. Trying manual connection.")
    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        pass
        
    # 3. Check again for 10 seconds
    for _ in range(10):
        try:
            check = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"], 
                                   capture_output=True, text=True, timeout=5)
            if any(line.startswith(f"yes:{ssid}") for line in check.stdout.strip().split('\n')):
                logger.info(f"Boot check: Manually connected to {ssid} successfully.")
                return True
        except Exception:
            pass
        time.sleep(1)
        
    logger.error(f"Boot check: Failed to connect to saved WiFi {ssid} after reboot.")
    return False


if __name__ == '__main__':
    # --- TESTING MODE: Factory Reset on Boot ---
    logger.info("TESTING MODE RESTART: Wiping ALL config for fresh test.")
    config.set("setup_complete", False)
    config.set("wifi_ssid", "")
    config.set("wifi_password", "")
    config.set("admin_email", "")
    config.set("admin_password_hash", "")
    config.set("session_secret", "")  # Invalidate all browser sessions
    # Re-generate a fresh secret key for this boot
    import secrets
    new_secret = secrets.token_hex(24)
    app.secret_key = new_secret
    config.set("session_secret", new_secret)
    try:
        stop_ap_hotspot()
    except Exception:
        pass
    # Also disconnect any existing WiFi so AP mode starts fresh
    try:
        subprocess.run(["nmcli", "device", "disconnect", "wlan0"],
                       capture_output=True, text=True, timeout=5)
    except Exception:
        pass
    # ---------------------------------------------

    # Do boot connectivity check first if we have a saved WiFi configuration
    if config.get("wifi_ssid"):
        if verify_or_connect_wifi_on_boot():
            logger.info("Boot check success. Marking setup as complete.")
            config.set("setup_complete", True)
        else:
            logger.error("Could not reach saved WiFi. Reverting to setup mode.")
            config.set("setup_complete", False)
    else:
        config.set("setup_complete", False)

    ip = _get_ip()
    print("=" * 60)
    print("  Vignette - H System Smart Display")
    print(f"  Output directory: {OUTPUT_DIR}")
    print(f"  Setup complete: {config.is_setup_complete}")
    print(f"  Local:   http://localhost:5000")
    print(f"  Network: http://{ip}:5000")
    if not config.is_setup_complete:
        print(f"  AP Mode: Connect to '{AP_SSID}' then open http://192.168.4.1:5000")
    print("=" * 60)

    # On first boot or failed reconnect: scan WiFi, then start AP
    if not config.is_setup_complete:
        logger.info("System in setup mode - scanning WiFi, starting AP, displaying QR setup")
        scan_and_cache_wifi()  # Scan BEFORE starting AP (wlan0 still in station mode)
        start_ap_hotspot()
        try:
            display_qr_setup()
        except Exception as e:
            logger.error(f"Could not display QR setup: {e}")
    else:
        # Start ngrok tunnel since network is available
        try:
            from pyngrok import ngrok
            logger.info("Initializing ngrok tunnel...")
            ngrok.set_auth_token("3By64a7MxTOJAWF3eD8TGoLcaIl_5tprPKw4ftjjRSTWU1eVM")
            public_url = ngrok.connect(5000).public_url
            logger.info(f"Ngrok Tunnel Active: {public_url}")
            
            # Display ngrok URL on e-paper
            ssid = get_active_ssid() or config.get("wifi_ssid", "WiFi")
            # We can override the IP display to show the ngrok tunnel so the user knows remote access is ready
            display_wifi_connected(ssid, ip_address=public_url.replace("https://", ""))
        except Exception as e:
            logger.error(f"Failed to start ngrok: {e}")
            try:
                ssid = get_active_ssid() or config.get("wifi_ssid", "WiFi")
                display_wifi_connected(ssid, ip)
            except Exception as e2:
                pass

    app.run(host='0.0.0.0', port=5000, debug=False)
