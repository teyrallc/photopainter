#!/usr/bin/env python3
"""
Vignette - Smart Display Web Control Interface
Flask web application for controlling the Waveshare 7.3" e-paper display.

Phase 2: Three page views (Home/Widget/Photo), Weather, Calendar,
         QR setup, photo rotation/fit, virtual buttons.
"""

import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import (Flask, jsonify, make_response, redirect, render_template,
                   request, send_file, send_from_directory, session, url_for)
from flask.sessions import SecureCookieSessionInterface
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

# Pillow compatibility
LANCZOS = getattr(Image, 'Resampling', Image).LANCZOS

# Project paths
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
LIB_DIR = os.path.join(PROJECT_DIR, "lib")
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
# Which iCloud photos this frame already holds. Deliberately not in
# config.json: an album can run to thousands of entries and config.json is
# rewritten whole on every settings change.
ICLOUD_LEDGER_PATH = os.path.join(PROJECT_DIR, "icloud_album.json")
GDRIVE_LEDGER_PATH = os.path.join(PROJECT_DIR, "gdrive_imported.json")

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
from services import weather as weather_svc
from services.weather import WeatherError
from services.calendar_svc import (fetch_calendar_events, forget_calendars,
                                   get_today_info)
from services.i18n import get_translations
from services import renderer
from services import gdrive
from services import icloud
from services import upload_token
from services.photo_ledger import PhotoLedger, digest_file
from services import display_mgr
from services import device_id
from services import epd as epd_service
from services.remote_access import service as remote_access
from services.net_watchdog import service as net_watchdog

config = Config(CONFIG_PATH)

app = Flask(__name__)

# The tunnel is a reverse proxy: without this, `request.host` is the local
# socket rather than the public hostname the browser actually used, and
# `request.is_secure` is False even though the connection was HTTPS. Both are
# load-bearing below — the same-origin check compares against the host, and
# the session cookie's Secure flag follows the scheme.
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
# `config.get` only falls back when the key is absent, so a stored empty string
# used to hand Flask an empty secret key and break every session. Treat blank
# as missing, and persist the generated secret so sign-ins survive a restart.
_session_secret = config.get("session_secret") or os.urandom(24).hex()
app.secret_key = _session_secret
if config.get("session_secret") != _session_secret:
    config.set("session_secret", _session_secret)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
# Without SameSite the session cookie rides along on cross-site requests, which
# is what turns any page the owner happens to visit into a remote control for
# this device.
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True


# ── Static assets carry their own version ────────────────────────────────
#
# The stylesheet and the scripts are served from fixed paths, so a browser that
# has them cached keeps using them after an update — and the new markup renders
# against the old CSS. That does not look like a caching problem to anybody; it
# looks like the update broke the console. It is why a settings page arrived
# with its rows unstacked and a QR code rendered at full column width.
#
# Stamping the file's mtime onto every url_for('static', …) makes the address
# change whenever the file does, which is the whole fix: same file, same URL,
# cached; changed file, new URL, fetched.
@app.url_defaults
def _version_static_assets(endpoint, values):
    if endpoint != 'static' or 'filename' not in values or 'v' in values:
        return
    try:
        stamp = os.stat(os.path.join(app.static_folder, values['filename'])).st_mtime
    except OSError:
        return          # a missing file is the 404's problem, not this hook's
    values['v'] = int(stamp)


@app.url_value_preprocessor
def _drop_static_version(endpoint, values):
    # The version is addressing, not an argument the view should see.
    if endpoint == 'static' and values:
        values.pop('v', None)


class SchemeAwareSessionInterface(SecureCookieSessionInterface):
    """Mark the session cookie Secure exactly when the connection was HTTPS.

    The same server answers on two very different paths: plain HTTP on the
    LAN, and HTTPS through the tunnel. A global SESSION_COOKIE_SECURE would
    have to pick one — set it and LAN sign-in breaks, leave it and the cookie
    that travels over the internet is not marked. Deciding per request is the
    only answer that is correct on both.
    """

    def get_cookie_secure(self, app):
        return bool(request and request.is_secure)


app.session_interface = SchemeAwareSessionInterface()

# A Pi Zero has well under a gigabyte of RAM, so an image that decompresses to
# a few hundred megapixels is a denial of service rather than a photo. Pillow's
# default only warns; cap it at something a 7.3" panel could ever need.
Image.MAX_IMAGE_PIXELS = 64 * 1024 * 1024

# ── Auth System ────────────────────────────────────────────────────────
from services import auth_mgr
auth_mgr.set_config_ref(config)

from auth import bp as auth_bp
app.register_blueprint(auth_bp)

# Reachable with no session in any state: assets and the sign-in/registration
# flow itself.
_ALWAYS_OPEN_PREFIXES = ('/static', '/auth')

# Reachable with no session *only while the device is unpaired*. This is the
# pairing portal and nothing else — the previous version opened the entire API
# during pairing, and left /api/wifi/* open forever afterwards, so anyone who
# reached the device could re-point it at their own access point or enumerate
# the owner's neighbouring networks long after setup finished.
_PAIRING_OPEN_EXACT = ('/', '/setup')
_PAIRING_OPEN_PREFIXES = ('/api/wifi/',)

# The one endpoint that must answer unauthenticated in both states: after the
# hotspot drops, the phone polls it on the device's new address to learn
# whether the join worked, and nobody can have signed in yet. It only reports
# the progress of a connection attempt the caller just made.
_CONNECT_STATUS_PATH = '/api/wifi/connect/status'

# The *only* endpoints an upload token opens, and only for POST. It is a
# credential that lives in an automation on somebody's phone, so it authorises
# sending a photo in and nothing else — not the config, not the WiFi, not the
# factory reset. Widening this list is a decision about what a stolen phone
# gets, so it is written out rather than derived from a prefix.
_UPLOAD_TOKEN_PATHS = ('/api/upload', '/api/upload/batch')

# The same credential, carried in the path instead of a header. A Shortcuts
# automation is built by hand on a phone screen, and "add a header, key
# Authorization, value Bearer <64 characters>" is the step people give up on —
# a URL they can paste in one action is the difference between a sync that gets
# set up and one that does not.
#
# The trade is real and deliberate: a token in a URL can end up in a proxy log
# or a Referer header in a way a header does not. It is bounded the same way
# the header form is — POST only, and it opens the upload endpoints and nothing
# else — and it is revocable from Settings the moment it leaks.
_UPLOAD_TOKEN_URL_PREFIX = '/api/upload/t/'


def _sync_url_token(path):
    """The token in a sync address, if this path is one and carries a whole one.

    Only the first segment counts: anything after it is a different route, and
    a token that "matches" with a suffix attached is not a match.
    """
    if not path.startswith(_UPLOAD_TOKEN_URL_PREFIX):
        return None
    candidate = path[len(_UPLOAD_TOKEN_URL_PREFIX):].strip('/')
    if not candidate or '/' in candidate:
        return None
    return candidate


def _is_scanned_sync_address(path):
    """A GET on a sync address — somebody who scanned the QR with their camera.

    Allowed through without a session whatever the token turns out to be, and
    deliberately so: the view renders a help page either way, and telling a
    revoked address apart from a live one is the entire point of letting the
    dead case render too. It performs nothing and discloses nothing the caller
    did not already have in their address bar.
    """
    return request.method == 'GET' and _sync_url_token(path) is not None


def _upload_token_authorised(path):
    """Does this request carry a valid upload token for an endpoint it opens?"""
    if request.method != 'POST':
        return False
    if path.startswith(_UPLOAD_TOKEN_URL_PREFIX):
        presented = _sync_url_token(path)
        if not presented:
            return False
    elif path in _UPLOAD_TOKEN_PATHS:
        presented = upload_token.from_request(request)
    else:
        return False
    if not upload_token.is_configured(config):
        return False
    if not presented:
        return False
    if not upload_token.verify(config, presented):
        logger.warning(f"Rejected an upload with a bad token from "
                       f"{request.remote_addr}")
        return False
    return True


@app.before_request
def enforce_auth():
    """Globally protect routes.

    Two states, deliberately different: while unpaired the pairing portal is
    open because there is no account to authenticate against yet; once paired,
    everything except the assets and the auth flow needs a session.
    """
    path = request.path

    if path.startswith(_ALWAYS_OPEN_PREFIXES) or path == _CONNECT_STATUS_PATH:
        return

    # A signed-in browser is not the only legitimate caller: an iPhone Shortcut
    # sends photos with a token instead. Checked after the always-open paths
    # and before the session, but only ever for the two upload endpoints.
    if _upload_token_authorised(path) or _is_scanned_sync_address(path):
        return

    if not config.is_setup_complete:
        if path in _PAIRING_OPEN_EXACT or path.startswith(_PAIRING_OPEN_PREFIXES):
            return
        # Anything else during pairing still falls through to the checks below,
        # so /api/config and friends are not an open door on a fresh device.

    if not config.get("admin_email"):
        if path.startswith('/api/'):
            return jsonify({"error": "Setup required."}), 401
        return redirect(url_for('auth.setup_admin'))

    if not session.get('logged_in'):
        if path.startswith('/api/'):
            return jsonify({"error": "Unauthorized.", "redirect": "/auth/login"}), 401
        return redirect(url_for('auth.login', next=request.url))


def _acceptable_hosts():
    """Hostnames a request may legitimately claim to come from.

    Normally just the host it arrived on. The tunnel host is included as well
    so that a browser which followed a redirect, or reached the device on the
    LAN while the tunnel is also up, is not mistaken for an attacker.
    """
    hosts = {request.host}
    forwarded = request.headers.get('X-Forwarded-Host')
    if forwarded:
        hosts.add(forwarded.split(',')[0].strip())
    tunnel = remote_access.host
    if tunnel:
        hosts.add(tunnel)
    # The configured address counts even while the tunnel reports itself down,
    # or a request that genuinely arrived through it would be rejected during
    # a reconnect.
    configured = remote_access.configured_host
    if configured:
        hosts.add(configured)
    return hosts


@app.before_request
def enforce_same_origin():
    """Reject cross-site state changes.

    Every mutating endpoint here is a plain JSON POST with no token, so a page
    on any other origin could drive this device — reboot it, factory-reset it,
    rewrite its config — using the owner's own session cookie. Comparing the
    request's declared origin against the host it actually arrived on costs
    nothing and closes that off; browsers set one of these headers on exactly
    the cross-origin requests we care about.
    """
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    if request.path == _CONNECT_STATUS_PATH:
        return

    origin = request.headers.get('Origin')
    source = origin or request.headers.get('Referer')
    if not source:
        # Non-browser clients (curl, the update script) send neither. They are
        # not the CSRF threat — that requires a browser carrying the cookie.
        return

    from urllib.parse import urlsplit
    if urlsplit(source).netloc not in _acceptable_hosts():
        logger.warning(f"Blocked cross-origin {request.method} {request.path} "
                       f"from {source!r} (host is {request.host!r})")
        return jsonify({"error": "Cross-origin request blocked."}), 403


@app.context_processor
def inject_globals():
    """Inject translation strings, language and device identity into templates.

    `ap_ssid` is here because the pairing hotspot's name is per device now, so
    no template or string table may hard-code it. The password deliberately is
    not: it belongs on the e-paper panel in front of whoever owns the device,
    not in a page a tunnelled browser session can read.
    """
    lang = request.cookies.get("lang", config.get("lang", "en"))
    return {
        "t": get_translations(lang),
        "current_lang": lang,
        "ap_ssid": AP_SSID,
        # Every page carries it, not just the ones handed `config`: the
        # e-paper frame is drawn on four of them and all four should be the
        # shape the panel actually is.
        "orientation": "portrait" if display_mgr.is_portrait() else "landscape",
    }


@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled error on {request.path}: {e}", exc_info=True)
    if request.path.startswith('/api/') or request.path.startswith('/auth/'):
        code = getattr(e, 'code', 500)
        # HTTPExceptions carry a description written for the caller; anything
        # else is an internal fault whose str() is a Python traceback message
        # and describes our internals, not the caller's mistake.
        if code == 500:
            return jsonify({"error": "Internal error. See the device log for details."}), 500
        return jsonify({"error": getattr(e, 'description', None) or "Request failed."}), code
    raise e


def safe_output_path(filename):
    """Resolve `filename` inside OUTPUT_DIR, or return None if it escapes.

    Endpoints that take the name from a JSON body rather than a route segment
    get no protection from the URL router, so '../../etc/passwd' arrived here
    intact and was happily opened.
    """
    if not filename:
        return None
    root = os.path.realpath(OUTPUT_DIR)
    candidate = os.path.realpath(os.path.join(root, filename))
    if candidate != root and not candidate.startswith(root + os.sep):
        logger.warning(f"Rejected path outside output dir: {filename!r}")
        return None
    return candidate


display_lock = threading.RLock()

# ── WiFi AP Hotspot Management ────────────────────────────────────────────

# Derived per device rather than fixed in the source: every unit used to
# advertise the same name with the same published password, so any two frames
# in one house were indistinguishable and either was joinable by anyone who
# had read this file.
AP_SSID, AP_PASSWORD = device_id.ap_credentials(config)
AP_CONN_NAME = device_id.AP_CONN_NAME

# Cached WiFi scan results (scanned before AP starts)
_cached_wifi_networks = []


# The service no longer runs as root, and NetworkManager will not let an
# unprivileged account create or activate connection profiles. `sudo nmcli` is
# on the narrow allowlist installed by scripts/install.sh; when we *are* root
# (a dev checkout, an older install) the prefix is skipped so nothing changes.
_NEEDS_SUDO = os.geteuid() != 0


def nmcli(*args, timeout=15, check=False):
    """Run one nmcli command, elevating only if we have to."""
    cmd = (["sudo", "-n", "nmcli"] if _NEEDS_SUDO else ["nmcli"]) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, check=check)


def _nmcli_fields(line):
    """Split one line of `nmcli -t` output into its fields.

    Terse mode escapes literal colons inside values as ``\\:``, so splitting on
    a plain ':' mangles any SSID that contains one and shifts every field after
    it — the signal strength lands in the security column and the network
    becomes unselectable. Unescape while splitting instead."""
    fields, current = [], []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '\\' and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
        elif ch == ':':
            fields.append(''.join(current))
            current = []
            i += 1
        else:
            current.append(ch)
            i += 1
    fields.append(''.join(current))
    return fields


def _nmcli_active_ssid(stdout):
    """Pull the connected SSID out of `nmcli -t -f ACTIVE,SSID dev wifi`.

    Kept only as the last resort in `get_active_ssid`. `dev wifi` reports
    *scan results*, and a scan that has not run yet lists nothing at all — so
    this says "not connected" for a link that is up and holding a lease. See
    `_wifi_device_status` for why that mattered.
    """
    for line in stdout.strip().split('\n'):
        parts = _nmcli_fields(line)
        if len(parts) >= 2 and parts[0] == "yes":
            return parts[1].strip()
    return None


def _wifi_device_status():
    """(device, state, profile) for the WiFi interface, from the link itself.

    `nmcli dev wifi` answers "which access points has a scan seen", which is a
    different question from "what am I connected to". After a cold boot the
    scan list can be empty for minutes while the interface is already
    associated — and the boot check read that emptiness as the saved network
    being unreachable, tore the working link down, and raised the pairing
    hotspot on a device that was online the whole time. `device status` reads
    the link, needs no scan, and answers immediately.

    Returns (None, None, None) when NetworkManager does not answer, which the
    caller must tell apart from a genuine "not connected".
    """
    try:
        result = nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION",
                       "device", "status", timeout=8)
    except Exception as e:
        logger.debug(f"nmcli device status failed: {e}")
        return None, None, None

    for line in result.stdout.strip().split('\n'):
        parts = _nmcli_fields(line)
        if len(parts) >= 4 and parts[1] == "wifi":
            return parts[0], parts[2], parts[3]
    return None, None, None


def _profile_ssid(profile):
    """The SSID a connection profile actually joins.

    Usually the profile name is the SSID, but not always: our own hotspot
    profile is called `Vignette-Hotspot` while it advertises `Vignette-XXXX`,
    and a profile the owner renamed would never match the saved SSID. Falls
    back to the profile name, which is right in the common case.
    """
    if not profile or profile == "--":
        return None
    try:
        result = nmcli("-t", "-f", "802-11-wireless.ssid",
                       "connection", "show", profile, timeout=8)
        for line in result.stdout.strip().split('\n'):
            parts = _nmcli_fields(line)
            if len(parts) >= 2 and parts[0] == "802-11-wireless.ssid":
                return parts[1].strip() or profile
    except Exception as e:
        logger.debug(f"Could not read the SSID of profile {profile!r}: {e}")
    return profile


def _parse_wifi_scan(stdout):
    """Parse `nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list` into a
    de-duplicated list, strongest signal first."""
    networks, seen = [], set()
    for line in stdout.strip().split('\n'):
        if not line:
            continue
        parts = _nmcli_fields(line)
        if len(parts) < 3 or not parts[0] or parts[0] in seen:
            continue
        seen.add(parts[0])
        try:
            signal = int(parts[1])
        except (TypeError, ValueError):
            # A non-numeric SIGNAL used to blow up the sort and lose the whole
            # scan; treat it as "unknown, weakest" instead.
            signal = 0
        networks.append({"ssid": parts[0], "signal": f"{signal}%",
                         "security": parts[2]})
    networks.sort(key=lambda n: int(n["signal"].rstrip('%')), reverse=True)
    return networks


def scan_and_cache_wifi():
    """Scan WiFi networks BEFORE starting AP. Cache results for the setup page.
    Must be called while wlan0 is in station mode (not AP)."""
    global _cached_wifi_networks
    try:
        logger.info("Scanning WiFi networks before starting AP...")
        result = nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes", timeout=30)
        _cached_wifi_networks = _parse_wifi_scan(result.stdout)
        logger.info(f"Cached {len(_cached_wifi_networks)} WiFi networks")
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
        nmcli("connection", "delete", AP_CONN_NAME, timeout=10)

        # Create connection profile with explicit settings
        result = nmcli("connection", "add",
            "type", "wifi",
            "ifname", "wlan0",
            "con-name", AP_CONN_NAME,
            "autoconnect", "no",
            "ssid", AP_SSID,
            "mode", "ap",
            "ipv4.method", "shared",
            "ipv4.addresses", "192.168.4.1/24",
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", AP_PASSWORD, timeout=15)

        if result.returncode != 0:
            logger.error(f"Failed to create hotspot profile: {result.stderr}")
            return False

        # Activate the connection
        result = nmcli("connection", "up", AP_CONN_NAME, timeout=15)

        if result.returncode == 0:
            # The password is deliberately not logged — the journal is
            # readable by anyone who can reach the device's shell.
            logger.info(f"AP hotspot started: SSID={AP_SSID}")
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
        nmcli("connection", "down", AP_CONN_NAME, timeout=10)
        nmcli("connection", "delete", AP_CONN_NAME, timeout=10)
        logger.info("AP hotspot stopped")
    except Exception as e:
        logger.error(f"Hotspot stop error: {e}")


def is_ap_active():
    """Check if the AP hotspot is currently running."""
    try:
        result = nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active", timeout=5)
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


# A stored photo is bounded by the panel's longest edge in *both* axes rather
# than fitted to its exact shape. Baking an 800x480 letterbox into the file
# made every stored photograph landscape, so turning the frame on its side
# showed a 480x288 strip adrift in a 480x800 page. Fit and stretch are decided
# when the page is drawn — they always were — so nothing is lost by not
# deciding them twice.
MAX_STORED_EDGE = max(EPD_WIDTH, EPD_HEIGHT)


def process_upload(file_storage, rotation=0):
    """Store an uploaded image, ready for the panel.

    Returns (filename, was_already_here). The second value is what lets a
    repeated upload answer "yes, I have that one" instead of quietly making a
    second copy — see services/photo_ledger.
    """
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

    # Land the upload in a temp file first. Writing straight into the gallery
    # meant a file that only *looked* like a PNG — the extension is all that
    # was ever checked — stayed behind in output/ after the decode blew up, and
    # every later listing counted it as a photo.
    fd, staging = tempfile.mkstemp(dir=OUTPUT_DIR, prefix=".incoming-", suffix=ext)
    os.close(fd)
    try:
        file_storage.save(staging)

        # Identity is the bytes, checked before any work is done on them. The
        # phone automation that watches an album re-offers everything inside
        # its time window on every run, so without this the gallery fills with
        # copies of the same photograph.
        digest = digest_file(staging)
        already = photo_index.lookup(digest)
        if already:
            os.remove(staging)
            return already, True

        # verify() settles what the bytes actually are; it also invalidates the
        # instance, so the real load happens on a second open.
        with Image.open(staging) as probe:
            probe.verify()

        img = Image.open(staging).convert("RGB")
        if rotation:
            img = img.rotate(-rotation, expand=True)
    except Exception:
        if os.path.exists(staging):
            os.remove(staging)
        raise

    try:
        img.thumbnail((MAX_STORED_EDGE, MAX_STORED_EDGE), LANCZOS)
        img.save(filepath)
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
        raise
    finally:
        img.close()
        if os.path.exists(staging):
            os.remove(staging)

    photo_index.remember(digest, filename)
    return filename, False


def reserve_output_name(preferred, fallback):
    """A free filename inside OUTPUT_DIR, and the path it resolves to.

    Shared by every import path (Google Drive, iCloud) so that a photo whose
    name collides with one already in the gallery lands beside it as
    `name_1.jpg` instead of overwriting it.
    """
    from werkzeug.utils import secure_filename

    safe_name = secure_filename(preferred or "") or secure_filename(fallback) or "photo.jpg"
    base, ext = os.path.splitext(safe_name)
    dest = os.path.join(OUTPUT_DIR, safe_name)
    counter = 1
    while os.path.exists(dest):
        safe_name = f"{base}_{counter}{ext}"
        dest = os.path.join(OUTPUT_DIR, safe_name)
        counter += 1
    return safe_name, dest


def fit_downloaded_image(path, rotation=0):
    """Reshape a freshly downloaded photo for the panel, in place.

    The bytes come off the internet, so they are verified before being decoded
    — an import used to leave whatever it fetched sitting in the gallery, where
    every later listing counted it as a photo even when it was not one.

    Bounded rather than fitted, for the reason given above MAX_STORED_EDGE.
    """
    with Image.open(path) as probe:
        probe.verify()                       # settles what the bytes really are

    img = Image.open(path).convert("RGB")
    try:
        if rotation:
            img = img.rotate(-rotation, expand=True)
        img.thumbnail((MAX_STORED_EDGE, MAX_STORED_EDGE), LANCZOS)
        img.save(path)
    finally:
        img.close()


# ── E-Paper Display Functions ──────────────────────────────────────────────

# Initialize display manager with our state and shared config
display_mgr.init_display_mgr(config, photo_state, get_current_photo_path)
display_state = display_mgr.display_state
display_lock = display_mgr.display_lock



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
                           config=config.public_dict(),
                           now=int(time.time()))


@app.route('/setup')
def setup_page():
    if not config.is_setup_complete:
        return render_template('wifi_setup.html')
    return render_template('setup.html', config=config.public_dict())



@app.route('/upload')
def upload_page():
    return render_template('upload.html', config=config.public_dict())


@app.route('/gallery')
def gallery_page():
    images = get_image_list()
    gdrive_connected = config.get("gdrive_connected", False)
    gdrive_configured = bool(config.get("gdrive_client_id", ""))
    return render_template('gallery.html', images=images, config=config.public_dict(),
                           gdrive_connected=gdrive_connected,
                           gdrive_configured=gdrive_configured,
                           icloud_connected=bool(config.get("icloud_connected")),
                           icloud_album=config.get("icloud_album_name", ""))


@app.route('/settings')
def settings_page():
    return render_template('settings.html', config=config.public_dict())


@app.route('/wifi')
def wifi_page():
    return render_template('wifi.html', config=config.public_dict())


@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Not found"}), 404


# ── API: Setup & Config ──────────────────────────────────────────────────

@app.route('/api/setup', methods=['POST'])
def api_setup():
    """Save initial setup configuration."""
    data = request.get_json() or {}
    saved = {
        "setup_complete": True,
        "weather_city": data.get("weather_city", ""),
        "weather_units": data.get("weather_units", "metric"),
        "calendar_ical_url": data.get("calendar_ical_url", ""),
        "current_page": data.get("current_page", "photo"),
    }
    # The form no longer echoes the stored key back, so an empty field means
    # "unchanged" rather than "clear it".
    if data.get("weather_api_key"):
        saved["weather_api_key"] = data["weather_api_key"]
    config.update(saved)
    weather_svc.clear_cache()
    logger.info("Setup complete!")
    return jsonify({"success": True, "message": "Setup saved"})


@app.route('/api/config', methods=['GET'])
def api_config_get():
    return jsonify(config.public_dict())


_WEATHER_KEYS = frozenset({"weather_api_key", "weather_city",
                           "weather_units", "weather_lang"})


@app.route('/api/config', methods=['POST'])
def api_config_set():
    data = request.get_json() or {}
    applied = config.apply_user_settings(data)
    # Saving a new city has to change what the next read returns. The cache is
    # keyed by the whole query so a changed setting is already a miss, but
    # dropping it here means the panel repaints with the new place rather than
    # finishing out the old entry's hour.
    if _WEATHER_KEYS & set(applied):
        weather_svc.clear_cache()
    # Same reasoning for the calendar: a feed added in Settings has to show up
    # in the Test button and on the next repaint, not a quarter of an hour
    # later when its cache happens to expire.
    if {"calendars", "calendar_ical_url"} & set(applied):
        forget_calendars()
    return jsonify({"success": True, "applied": sorted(applied),
                    "config": config.public_dict()})


@app.route('/api/reset', methods=['POST'])
def api_reset():
    """Reset all settings to factory defaults (simulates first-time QR setup)."""
    data = request.get_json(silent=True) or {}

    # A device that changes hands keeps the previous owner's photos unless the
    # caller asks for them too — config.reset() only ever cleared settings.
    removed = 0
    if data.get("delete_photos"):
        for image in get_image_list():
            try:
                os.remove(os.path.join(OUTPUT_DIR, image["filename"]))
                removed += 1
            except OSError as e:
                logger.error(f"Factory reset: could not delete {image['filename']}: {e}")
        photo_state.update({"current_index": -1, "current_image": None, "total": 0})
        logger.info(f"Factory reset: deleted {removed} photos")

    # The album link is a credential of sorts, and the ledger names photos the
    # previous owner shared; neither belongs to whoever gets the frame next.
    icloud.forget_album()
    icloud_ledger.clear()

    config.reset()
    logger.info("System reset to factory defaults")
    # Start AP hotspot for WiFi configuration
    start_ap_hotspot()
    # Display QR setup on e-paper
    display_mgr.display_qr_setup()
    return jsonify({"success": True, "photos_deleted": removed,
                    "message": "Reset complete. QR setup displayed."})


# ── API: Upload token (for the iPhone Shortcut) ──────────────────────────

@app.route('/api/upload-token')
def api_upload_token_status():
    """Whether a token exists — never the token itself."""
    return jsonify({
        "configured": upload_token.is_configured(config),
        "created": upload_token.created_at(config),
        # What the Shortcut has to be pointed at. The tunnel address is the
        # useful one: an automation on a phone that has left the house still
        # has to reach the frame.
        "upload_url": (remote_access.url or f"http://{_get_ip()}:5000") + "/api/upload",
    })


@app.route('/api/upload-token', methods=['POST'])
def api_upload_token_mint():
    """Mint a token, replacing any existing one.

    This is the one and only response that carries the token itself; it is
    stored hashed, so nothing can hand it back later.
    """
    token = upload_token.mint(config)
    base = remote_access.url or f"http://{_get_ip()}:5000"
    return jsonify({
        "success": True,
        "token": token,
        "created": upload_token.created_at(config),
        "upload_url": base + "/api/upload",
        # The whole credential in one address. This is what the phone
        # automation is pointed at, and the only moment it can be shown —
        # storage keeps a hash, so it cannot be handed back later.
        "sync_url": f"{base}/api/upload/t/{token}",
    })


@app.route('/api/qr')
def api_qr():
    """A QR code for a short string, as a PNG.

    Only for getting an address off this screen and onto a phone, which is the
    difference between a sync somebody sets up and one they give up on halfway
    through typing. The text is whatever the caller passes and is never stored;
    the route needs a session like everything else.
    """
    text = (request.args.get("text") or "").strip()
    if not text or len(text) > 512:
        return jsonify({"error": "Nothing to encode."}), 400
    try:
        import qrcode
    except ImportError:
        return jsonify({"error": "qrcode is not installed."}), 501

    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    # An address with a credential in it has no business in a shared cache.
    response = send_file(buf, mimetype="image/png")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route('/api/upload-token', methods=['DELETE'])
def api_upload_token_revoke():
    """Revoke the token. Any Shortcut carrying it stops working at once."""
    upload_token.revoke(config)
    return jsonify({"success": True})


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
        success, msg = display_mgr.display_current_page()
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
        success, msg = display_mgr.display_qr_setup()
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

def _show_after_upload(filename):
    """Put a just-uploaded photo on the panel, without holding the request.

    A panel refresh takes the better part of twenty seconds. Doing it inline
    would leave a phone staring at a spinner for the whole repaint — and if the
    Shortcut gave up first, the photo would still be landing. So the response
    goes back immediately and the paint happens behind it, which is the same
    shape the OTP and post-sign-in repaints already use.
    """
    path = safe_output_path(filename)
    if not path or not os.path.isfile(path):
        return

    def paint():
        # Wait rather than skip: something else holding the panel for a moment
        # is normal, and the photo somebody just sent should still arrive.
        if not display_lock.acquire(timeout=90):
            logger.warning(f"Panel stayed busy; {filename} was not displayed")
            return
        try:
            ok, message = display_mgr.display_image_on_epaper(path)
            if ok:
                for index, image in enumerate(get_image_list()):
                    if image["filename"] == filename:
                        photo_state["current_index"] = index
                        photo_state["current_image"] = filename
                        break
                config.set("current_page", "photo")
            else:
                logger.error(f"Could not display {filename}: {message}")
        except Exception as e:  # noqa: BLE001 - a background paint must not vanish silently
            logger.error(f"Could not display {filename}: {e}", exc_info=True)
        finally:
            display_lock.release()

    threading.Thread(target=paint, daemon=True).start()


# Same handler, two doors. The token-in-the-path form exists so a Shortcuts
# automation can be built from one pasted URL instead of a hand-typed
# Authorization header — see _upload_token_authorised for the boundary.
@app.route('/api/upload/t/<token>', methods=['GET'])
def sync_address_page(token):
    """What a scanned QR code lands on.

    The camera opens the address with a GET, and the upload route answers those
    with "the method is not allowed" — a raw JSON error, on the phone, at the
    exact moment somebody is trying to follow the setup. So the address renders
    a page instead: the address itself, a button that copies it, and the steps
    it is needed for. It performs nothing.

    Reached without a session on purpose: the phone scanning it is not signed
    in, and the address in the bar is the credential. Anyone holding it can
    already upload, so showing it back to them discloses nothing new.
    """
    if not (upload_token.is_configured(config) and upload_token.verify(config, token)):
        # A revoked or mistyped address must not look like a working one.
        return render_template('sync_address.html', address=None), 404
    return render_template('sync_address.html', address=request.url)


@app.route('/api/upload', methods=['POST'])
@app.route('/api/upload/t/<token>', methods=['POST'])
def api_upload(token=None):
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed"}), 400

    rotation = int(request.form.get('rotation', 0))
    show_now = request.form.get('display', '') in ('1', 'true', 'yes')

    try:
        filename, duplicate = process_upload(file, rotation)
        # A repeated photo is a success, not an error: the phone automation
        # re-offers everything in its time window on every run, and answering
        # 200 is what keeps its log clean.
        if show_now and not duplicate:
            _show_after_upload(filename)
        return jsonify({"success": True, "filename": filename,
                        "duplicate": duplicate,
                        "displaying": show_now and not duplicate,
                        "message": (f"Already on the frame: {filename}" if duplicate
                                    else f"Image uploaded: {filename}")})
    except UnidentifiedImageError:
        # The extension said PNG but the bytes disagree. That is the caller's
        # mistake, not a server fault.
        logger.warning(f"Rejected non-image upload: {file.filename!r}")
        return jsonify({"error": "That file is not a readable image."}), 400
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        return jsonify({"error": "Upload failed. See the device log for details."}), 500


@app.route('/api/display', methods=['POST'])
def api_display():
    data = request.get_json() or {}
    filename = data.get('filename') or request.form.get('filename')
    if not filename:
        return jsonify({"error": "No filename provided"}), 400
    filepath = safe_output_path(filename)
    if not filepath or not os.path.isfile(filepath):
        return jsonify({"error": "Image not found"}), 404
    # Name is re-derived from the resolved path so photo_state can never record
    # something the caller typed.
    filename = os.path.basename(filepath)
    if not display_lock.acquire(blocking=False):
        return jsonify({"error": "Display is busy"}), 503
    try:
        success, msg = display_mgr.display_image_on_epaper(filepath)
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
    filepath = safe_output_path(filename)
    if not filepath or not os.path.isfile(filepath):
        return jsonify({"error": "Image not found"}), 404
    buf = quantize_to_epaper(filepath)
    return send_file(buf, mimetype='image/png', download_name=f"preview_{filename}")


@app.route('/api/images')
def api_images():
    return jsonify(get_image_list())


@app.route('/api/images/<filename>', methods=['DELETE'])
def api_delete_image(filename):
    filepath = safe_output_path(filename)
    if not filepath or not os.path.isfile(filepath):
        return jsonify({"error": "Image not found"}), 404
    os.remove(filepath)
    return jsonify({"success": True, "message": f"Deleted {os.path.basename(filepath)}"})


@app.route('/api/upload/batch', methods=['POST'])
def api_upload_batch():
    """Upload multiple images at once."""
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "No files provided"}), 400

    rotation = int(request.form.get('rotation', 0))

    results = []
    for file in files:
        if file.filename == '' or not allowed_file(file.filename):
            results.append({"filename": file.filename, "success": False,
                            "error": "Invalid file"})
            continue
        try:
            filename, duplicate = process_upload(file, rotation)
            results.append({"filename": filename, "success": True,
                            "duplicate": duplicate})
        except UnidentifiedImageError:
            results.append({"filename": file.filename, "success": False,
                            "error": "Not a readable image"})
        except Exception as e:
            logger.error(f"Batch upload failed for {file.filename!r}: {e}", exc_info=True)
            results.append({"filename": file.filename, "success": False,
                            "error": "Upload failed"})

    ok = sum(1 for r in results if r["success"] and not r.get("duplicate"))
    skipped = sum(1 for r in results if r.get("duplicate"))
    return jsonify({"success": True, "uploaded": ok, "duplicates": skipped,
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

    results = []
    for f in files:
        file_id = f.get("id")
        name = f.get("name", f"{file_id}.jpg")
        if not file_id:
            continue

        safe_name, dest = reserve_output_name(name, f"gdrive_{file_id}.jpg")

        ok = gdrive.download_file(token, file_id, dest)
        if ok:
            try:
                fit_downloaded_image(dest, rotation)
                results.append({"name": safe_name, "success": True})
            except Exception as e:
                logger.error(f"Failed to process {safe_name}: {e}")
                if os.path.exists(dest):
                    os.remove(dest)
                results.append({"name": safe_name, "success": False, "error": str(e)})
        else:
            results.append({"name": name, "success": False, "error": "Download failed"})

    ok_count = sum(1 for r in results if r["success"])
    return jsonify({"success": True, "downloaded": ok_count,
                    "total": len(results), "results": results})


# ── API: iCloud Shared Album ──────────────────────────────────────────────
#
# The same shape as Google Drive above — connect, browse, import — but with no
# account to sign in to: an album with "Public Website" turned on is readable
# from the link alone, which is the only kind of credential worth typing on a
# device with no keyboard. The extra piece is the sync loop at the bottom, so
# a photo added on somebody's phone reaches the frame on its own.

icloud_ledger = icloud.ImportLedger(ICLOUD_LEDGER_PATH)

# Which photographs the frame already holds, keyed by their contents. The
# phone automation re-offers a whole day's photos on every run, so this is
# what stops the gallery filling with copies of the same picture.
photo_index = PhotoLedger(OUTPUT_DIR)

# The album is read on a background thread as well as from requests; one
# import at a time keeps two of them from writing the same photo twice.
_icloud_import_lock = threading.Lock()


def _icloud_token():
    return config.get("icloud_album_token", "") or ""


def _upstream_status(exc):
    """Map a service failure onto a status code.

    Anything the owner can fix — a link that is not a link, a rejected
    credential — is their 4xx, not our 5xx; only an upstream that is genuinely
    misbehaving or unreachable is a 502.
    """
    status = getattr(exc, "status", None)
    if status in (401, 403):
        # The link (or the API key) *is* the credential here, so a refusal is
        # something to fix in Settings rather than a server fault.
        return 400
    if status and 400 <= status < 500:
        return status
    return 502


def _icloud_error(exc):
    """One place that decides what an iCloud failure looks like on the wire."""
    return jsonify({"error": str(exc)}), _upstream_status(exc)


def _icloud_import(album, photos, rotation=None):
    """Download `photos` from `album` into the gallery. Returns a summary.

    Only ever called with entries that came from a listing this process
    fetched: the URLs are Apple's, signed and short-lived, and are never taken
    from the browser.
    """
    rotation = config.get("photo_rotation", 0) if rotation is None else rotation

    imported, failed, duplicates = [], [], []
    with _icloud_import_lock:
        icloud_ledger.bind(album["token"])
        for photo in photos:
            guid = photo.get("guid")
            if not guid or icloud_ledger.has(guid):
                continue

            name, dest = reserve_output_name(icloud.suggested_filename(photo),
                                             f"icloud_{len(imported)}.jpg")
            if not icloud.download_asset(photo.get("url"), dest):
                failed.append({"guid": guid, "error": "Download failed"})
                continue

            # The album's own record is not the only way a photograph gets
            # here: the same picture may already have arrived from the phone
            # shortcut, or from an album connected before this one. The ledger
            # is keyed on the bytes *as received* — the same convention
            # process_upload uses, so the two sources share one namespace —
            # and the guid is still recorded, so the album never downloads it
            # twice again.
            digest = digest_file(dest)
            existing = photo_index.lookup(digest) if digest else None
            # Never the file just written. reserve_output_name only ever hands
            # back a name nothing occupies, so a ledger entry naming *this*
            # file is a stale one that the download has accidentally made look
            # live again — and treating it as a duplicate deleted the only
            # copy and imported nothing.
            if existing == name:
                photo_index.forget_missing()
                existing = None
            if existing:
                os.remove(dest)
                icloud_ledger.record(guid, existing)
                duplicates.append(existing)
                continue

            try:
                fit_downloaded_image(dest, rotation)
            except Exception as e:  # noqa: BLE001 - one bad photo, not the album
                logger.error(f"iCloud: could not process {name}: {e}")
                if os.path.exists(dest):
                    os.remove(dest)
                failed.append({"guid": guid, "error": "Not a readable image"})
                continue
            # The digest from before fitting, which is what was looked up and
            # what an upload of the same photograph would present. Recording
            # the fitted bytes instead would have made every lookup miss.
            if digest:
                photo_index.remember(digest, name)
            icloud_ledger.record(guid, name)
            imported.append(name)

    if imported or duplicates:
        logger.info(f"iCloud: imported {len(imported)} photo(s), "
                    f"{len(duplicates)} already here")
    return {"imported": len(imported), "failed": len(failed),
            "duplicates": len(duplicates),
            "names": imported, "errors": failed}


def _icloud_sync():
    """Bring in every album photo the frame does not already have."""
    token = _icloud_token()
    if not token:
        raise icloud.ICloudError("No iCloud album is connected.")

    album = icloud.fetch_album(token, refresh=True)
    # A photo deleted from the gallery should be able to come back on the next
    # sync rather than being remembered forever as "already imported".
    icloud_ledger.bind(album["token"])
    icloud_ledger.prune({image["filename"] for image in get_image_list()})

    summary = _icloud_import(album, album["photos"])
    summary["album"] = album["name"]
    summary["total"] = len(album["photos"])

    config.update({
        "icloud_album_name": album["name"],
        "icloud_last_sync": datetime.now().isoformat(timespec="seconds"),
        "icloud_last_error": "",
    })
    return summary


def _icloud_status():
    return {
        "connected": bool(config.get("icloud_connected")),
        "album_url": config.get("icloud_album_url", ""),
        "album_name": config.get("icloud_album_name", ""),
        "auto_sync": bool(config.get("icloud_auto_sync", True)),
        "interval": int(config.get("icloud_sync_interval", 3600) or 0),
        "last_sync": config.get("icloud_last_sync", ""),
        "last_error": config.get("icloud_last_error", ""),
        "imported": icloud_ledger.count,
    }


@app.route('/api/icloud/status')
def api_icloud_status():
    return jsonify(_icloud_status())


@app.route('/api/icloud/connect', methods=['POST'])
def api_icloud_connect():
    """Attach a shared album, after checking that it actually answers."""
    data = request.get_json() or {}
    link = (data.get("url") or "").strip()
    try:
        token = icloud.parse_album_token(link)
        album = icloud.fetch_album(token, refresh=True)
    except icloud.ICloudError as exc:
        config.set("icloud_last_error", str(exc))
        return _icloud_error(exc)

    icloud_ledger.bind(token)
    config.update({
        # The album says which link shape it actually came from — the two
        # backends have different canonical URLs.
        "icloud_album_url": album.get("url") or icloud.album_url(token),
        "icloud_album_token": token,
        "icloud_album_name": album["name"],
        "icloud_connected": True,
        "icloud_last_error": "",
    })
    logger.info(f"iCloud album connected: {album['name']!r} "
                f"({len(album['photos'])} photos)")

    result = {"success": True, "album": album["name"], "owner": album["owner"],
              "photos": len(album["photos"]), "status": _icloud_status()}

    # "Connect and fill the frame" is the common case, so it is one request.
    if data.get("import_all"):
        result["import"] = _icloud_import(album, album["photos"])
        result["status"] = _icloud_status()
    return jsonify(result)


@app.route('/api/icloud/disconnect', methods=['POST'])
def api_icloud_disconnect():
    """Forget the album. Photos already imported stay in the gallery."""
    icloud.forget_album(_icloud_token())
    icloud_ledger.clear()
    config.update({
        "icloud_album_url": "",
        "icloud_album_token": "",
        "icloud_album_name": "",
        "icloud_connected": False,
        "icloud_last_error": "",
        "icloud_last_sync": "",
    })
    return jsonify({"success": True})


@app.route('/api/icloud/photos')
def api_icloud_photos():
    """List the album, marking what the frame already holds."""
    token = _icloud_token()
    if not token:
        return jsonify({"error": "No iCloud album is connected."}), 401
    try:
        album = icloud.fetch_album(
            token, refresh=request.args.get("refresh") in ("1", "true", "yes"))
    except icloud.ICloudError as exc:
        config.set("icloud_last_error", str(exc))
        return _icloud_error(exc)

    imported = icloud_ledger.guids()
    return jsonify({
        "album": album["name"],
        "owner": album["owner"],
        "photos": [{
            "guid": p["guid"],
            "caption": p["caption"],
            "created": p["created"],
            "width": p["width"],
            "height": p["height"],
            # Apple's own CDN URL: the browser fetches the thumbnail directly
            # rather than making a Pi Zero proxy every tile.
            "thumb": p["thumb"],
            "imported": p["guid"] in imported,
        } for p in album["photos"]],
    })


@app.route('/api/icloud/import', methods=['POST'])
def api_icloud_import():
    """Import selected album photos — or everything not yet imported."""
    token = _icloud_token()
    if not token:
        return jsonify({"error": "No iCloud album is connected."}), 401

    data = request.get_json() or {}
    guids = data.get("guids") or []
    try:
        album = icloud.fetch_album(token)
        if data.get("all") or not guids:
            photos = album["photos"]
        else:
            wanted = set(guids)
            photos = [p for p in album["photos"] if p["guid"] in wanted]
            if not photos:
                return jsonify({"error": "Those photos are no longer in the album."}), 404
        summary = _icloud_import(album, photos)
    except icloud.ICloudError as exc:
        config.set("icloud_last_error", str(exc))
        return _icloud_error(exc)

    if data.get("start_slideshow"):
        config.set("slideshow_active", True)
        start_slideshow_thread()
        summary["slideshow"] = True

    summary["success"] = True
    summary["status"] = _icloud_status()
    return jsonify(summary)


# ── API: Refresh every connected source ──────────────────────────────────
#
# One button on the gallery, because the owner does not think of it as "sync
# the album" and "pull from Drive" — they think "go and look for new
# photographs". What each source means by that differs, so each answers for
# itself and the summary says which brought what.

# A Drive holds everything anybody has ever put in it, including screenshots
# and scans. Only the newest handful is considered on each press, so pressing
# the button cannot empty somebody's Drive onto a photo frame.
GDRIVE_SYNC_LIMIT = 25

gdrive_ledger = icloud.ImportLedger(GDRIVE_LEDGER_PATH)


def _gdrive_sync():
    """Bring in the newest Drive images this frame has not imported before."""
    token = _gdrive_access_token()
    if not token:
        raise icloud.ICloudError("Not connected to Google Drive.", status=401)

    listing = gdrive.list_images(token, page_size=GDRIVE_SYNC_LIMIT)
    if listing.get("error"):
        config.set("gdrive_access_token", "")
        raise icloud.ICloudError(f"Google Drive: {listing['error']}", status=401)

    # A photo deleted from the gallery should be able to come back, exactly as
    # it can for an album.
    gdrive_ledger.bind(config.get("admin_email", "") or "drive")
    gdrive_ledger.prune({image["filename"] for image in get_image_list()})

    rotation = config.get("photo_rotation", 0)
    imported, duplicates, failed = [], [], []
    for entry in (listing.get("files") or [])[:GDRIVE_SYNC_LIMIT]:
        file_id = entry.get("id")
        if not file_id or gdrive_ledger.has(file_id):
            continue

        name, dest = reserve_output_name(entry.get("name") or f"{file_id}.jpg",
                                         f"gdrive_{file_id}.jpg")
        if not gdrive.download_file(token, file_id, dest):
            failed.append({"id": file_id, "error": "Download failed"})
            continue

        # Same three-way guard the album import uses: the bytes decide, so a
        # photograph that arrived from a phone or an album is not fetched
        # again under a Drive file's name.
        digest = digest_file(dest)
        existing = photo_index.lookup(digest) if digest else None
        if existing == name:
            photo_index.forget_missing()
            existing = None
        if existing:
            os.remove(dest)
            gdrive_ledger.record(file_id, existing)
            duplicates.append(existing)
            continue

        try:
            fit_downloaded_image(dest, rotation)
        except Exception as exc:  # noqa: BLE001 - one bad file, not the sync
            logger.error(f"Drive: could not process {name}: {exc}")
            if os.path.exists(dest):
                os.remove(dest)
            failed.append({"id": file_id, "error": "Not a readable image"})
            continue

        if digest:
            photo_index.remember(digest, name)
        gdrive_ledger.record(file_id, name)
        imported.append(name)

    if imported or duplicates:
        logger.info(f"Drive: imported {len(imported)}, "
                    f"{len(duplicates)} already here")
    return {"imported": len(imported), "duplicates": len(duplicates),
            "failed": len(failed), "names": imported}


@app.route('/api/sources/refresh', methods=['POST'])
def api_sources_refresh():
    """Look for new photographs in everything that is connected.

    Never fails as a whole because one source did: a broken album must not
    stop Drive being read, and the owner is told which one complained.
    """
    sources, errors = {}, []
    imported = duplicates = 0

    if _icloud_token():
        try:
            summary = _icloud_sync()
            sources["icloud"] = summary
            imported += summary.get("imported", 0)
            duplicates += summary.get("duplicates", 0)
        except icloud.ICloudError as exc:
            config.set("icloud_last_error", str(exc))
            errors.append(f"iCloud: {exc}")

    if config.get("gdrive_connected"):
        try:
            summary = _gdrive_sync()
            sources["gdrive"] = summary
            imported += summary["imported"]
            duplicates += summary["duplicates"]
        except icloud.ICloudError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - report, do not 500
            logger.error(f"Drive refresh failed: {exc}", exc_info=True)
            errors.append(f"Google Drive: {exc}")

    if not sources and not errors:
        return jsonify({"error": "No photo source is connected."}), 400
    return jsonify({"success": True, "imported": imported,
                    "duplicates": duplicates, "sources": sources,
                    "errors": errors})


@app.route('/api/icloud/sync', methods=['POST'])
def api_icloud_sync():
    """Pull anything new since the last sync."""
    if not _icloud_token():
        return jsonify({"error": "No iCloud album is connected."}), 401
    try:
        summary = _icloud_sync()
    except icloud.ICloudError as exc:
        config.set("icloud_last_error", str(exc))
        return _icloud_error(exc)
    summary["success"] = True
    summary["status"] = _icloud_status()
    return jsonify(summary)


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
        success, msg = display_mgr.display_test_pattern()
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
        epd = epd_service.get_epd(config.get("epd_model"))
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
        epd = epd_service.get_epd(config.get("epd_model"))
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
    events = []
    weather = weather_svc.fetch_for_config(config)
    if config.get("calendars"):
        events = fetch_calendar_events(config.get("calendars"))

    photo_path = get_current_photo_path()

    # Composed the way the frame is hung, and returned that way. The panel
    # gets the same page turned once more so it comes out upright on the wall;
    # a browser looking at the preview is already upright, so turning it here
    # too would show the owner a picture lying on its side.
    portrait = display_mgr.is_portrait()
    if page == "home":
        img = renderer.render_home_page(weather, events, photo_path, config,
                                        portrait=portrait)
    elif page == "widget":
        mode = config.get("widget_mode", "weather")
        img = renderer.render_widget_page(mode, weather, events, portrait=portrait)
    else:
        rotation = config.get("photo_rotation", 0)
        fit_mode = config.get("photo_fit_mode", "fit")
        img = renderer.render_photo_page(photo_path, rotation, fit_mode,
                                         portrait=portrait)

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
                display_mgr.display_current_page()
            except Exception as e:
                logger.error(f"Slideshow update failed: {e}")
            finally:
                display_lock.release()

    logger.info("Slideshow stopped")


def start_slideshow_thread():
    """(Re)start the slideshow worker. Safe to call when one is already running."""
    global slideshow_thread
    if slideshow_thread and slideshow_thread.is_alive():
        _slideshow_stop.set()
        slideshow_thread.join(timeout=5)

    _slideshow_stop.clear()
    slideshow_thread = threading.Thread(target=_slideshow_loop, daemon=True)
    slideshow_thread.start()


@app.route('/api/slideshow/start', methods=['POST'])
def api_slideshow_start():
    """Start photo slideshow."""
    data = request.get_json() or {}

    # Save slideshow config
    if "photos" in data:
        config.set("slideshow_photos", data["photos"])  # list of filenames, [] = all
    if "interval" in data:
        config.set("slideshow_interval", max(int(data["interval"]), 30))  # min 30s
    if "order" in data:
        config.set("slideshow_order", data["order"])  # "sequential" or "random"

    config.set("slideshow_active", True)
    start_slideshow_thread()

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


# ── Periodic panel refresh ──────────────────────────────────────────────
#
# The weather and calendar caches were written against an "hourly e-paper
# refresh" that was never actually built, so a wall-mounted panel showed
# whatever the data looked like the last time somebody pressed a button. This
# is that missing loop. Only the data-driven pages are redrawn — repainting a
# photo that has not changed would burn panel refresh cycles for nothing.

_refresh_stop = threading.Event()
refresh_thread = None

# Pages whose content goes stale on its own.
_DATA_PAGES = ("home", "widget")


def _auto_refresh_loop():
    logger.info("Auto-refresh loop started")
    while not _refresh_stop.is_set():
        interval = int(config.get("auto_refresh_interval", 3600) or 0)
        if interval <= 0:
            # Disabled — idle cheaply and pick the change up if it is re-enabled.
            _refresh_stop.wait(60)
            continue

        _refresh_stop.wait(interval)
        if _refresh_stop.is_set():
            break

        if config.get("current_page") not in _DATA_PAGES:
            continue
        if config.get("slideshow_active"):
            # The slideshow is already driving the panel; two writers would
            # just fight over the lock.
            continue

        if display_lock.acquire(blocking=False):
            try:
                logger.info("Auto-refresh: redrawing the current page")
                display_mgr.display_current_page()
            except Exception as e:
                logger.error(f"Auto-refresh failed: {e}")
            finally:
                display_lock.release()
        else:
            logger.info("Auto-refresh skipped: display busy")
    logger.info("Auto-refresh loop stopped")


def start_auto_refresh():
    global refresh_thread
    if refresh_thread and refresh_thread.is_alive():
        return
    _refresh_stop.clear()
    refresh_thread = threading.Thread(target=_auto_refresh_loop, daemon=True)
    refresh_thread.start()


# ── iCloud album sync ───────────────────────────────────────────────────
#
# The point of connecting an album rather than uploading files: somebody adds
# a photo from their phone and it appears on the frame. That only happens if
# something checks, so this is the thing that checks.

_icloud_stop = threading.Event()
icloud_sync_thread = None

# A shared album is not a busy feed, and each check costs the Pi a round trip
# plus a download per new photo. Ten minutes is as often as it is worth asking.
MIN_ICLOUD_INTERVAL = 600


def _icloud_sync_loop():
    logger.info("iCloud sync loop started")
    while not _icloud_stop.is_set():
        interval = max(int(config.get("icloud_sync_interval", 3600) or 0),
                       MIN_ICLOUD_INTERVAL)
        _icloud_stop.wait(interval)
        if _icloud_stop.is_set():
            break

        if not config.get("icloud_auto_sync", True) or not _icloud_token():
            continue
        try:
            summary = _icloud_sync()
            if summary["imported"]:
                logger.info(f"iCloud sync: {summary['imported']} new photo(s) "
                            f"from {summary['album']!r}")
        except icloud.ICloudError as e:
            # Expected while the house WiFi is down. Record it for the console
            # and try again at the next tick rather than stopping the loop.
            logger.warning(f"iCloud sync failed: {e}")
            config.set("icloud_last_error", str(e))
        except Exception as e:  # noqa: BLE001 - a sync fault must not kill the thread
            logger.error(f"iCloud sync error: {e}", exc_info=True)
            config.set("icloud_last_error", str(e))
    logger.info("iCloud sync loop stopped")


def start_icloud_sync():
    global icloud_sync_thread
    if icloud_sync_thread and icloud_sync_thread.is_alive():
        return
    _icloud_stop.clear()
    icloud_sync_thread = threading.Thread(target=_icloud_sync_loop, daemon=True)
    icloud_sync_thread.start()


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
        result = nmcli("-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi", timeout=10)
        for line in result.stdout.strip().split('\n'):
            parts = _nmcli_fields(line)
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
        result = nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes", timeout=30)
        return jsonify({"networks": _parse_wifi_scan(result.stdout)})
    except Exception as e:
        return jsonify({"networks": [], "error": str(e)})


# Background WiFi connection state (shared between request handler and bg thread)
_wifi_connect_state = {
    "status": "idle",       # idle | connecting | success | failed
    "ssid": "",
    "ip": None,
    "error": None,
}


def _await_remote_url(timeout=20):
    """Wait briefly for the tunnel to come up, then give up gracefully.

    Used only where a URL is about to be shown to somebody who is standing in
    front of the device. Never blocks the supervisor, which keeps retrying
    regardless of what happens here.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = remote_access.url
        if url:
            return url
        time.sleep(1)
    return remote_access.url


def _on_remote_url_change(url):
    """Repaint the panel when the public address changes.

    Free ngrok hands out a different hostname on every reconnect, so without
    this the address printed on the panel silently stops working. A Cloudflare
    tunnel has a fixed address, so this fires once and then never again —
    which is the point of preferring it.
    """
    if not config.is_setup_complete:
        return
    try:
        ssid = get_active_ssid() or config.get("wifi_ssid", "WiFi")
        display_mgr.display_wifi_connected(ssid, _get_ip(), remote_url=url)
    except Exception as e:
        logger.error(f"Could not repaint the panel for the new remote URL: {e}")


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
        nmcli("connection", "delete", ssid, timeout=5)

        # Build the connection profile non-interactively
        add_args = [
            "connection", "add",
            "type", "wifi",
            "ifname", "wlan0",
            "con-name", ssid,
            "ssid", ssid,
        ]
        if password:
            add_args += [
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", password,
            ]

        add_result = nmcli(*add_args, timeout=10)
        # stderr can echo the profile back, password included, so only the
        # outcome goes in the journal.
        logger.info(f"Background WiFi: nmcli add returned {add_result.returncode}")
        if add_result.returncode != 0:
            logger.warning("Background WiFi: could not create the connection profile")

        # Activate the connection (non-interactive, no agent needed)
        try:
            result = nmcli("connection", "up", ssid, timeout=30)
            logger.info(f"Background WiFi: nmcli up result: {result.stdout.strip()} {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            logger.warning("Background WiFi: nmcli timed out, checking connection status")
            result = None

        # Step 4: Verify connection (nmcli can return failure/timeout but still connect)
        is_connected = False
        for _ in range(5):  # Poll up to 10 seconds
            check = nmcli("-t", "-f", "ACTIVE,SSID", "dev", "wifi", timeout=5)
            for line in check.stdout.strip().split('\n'):
                parts = _nmcli_fields(line)
                if len(parts) >= 2 and parts[0] == "yes" and parts[1] == ssid:
                    is_connected = True
                    break
            if is_connected:
                break
            time.sleep(2)

        if is_connected:
            logger.info(f"Background WiFi: Successfully connected to {ssid}")

            # Ensure NM saves this connection with autoconnect and high priority
            nmcli("connection", "modify", ssid,
                 "connection.autoconnect", "yes",
                 "connection.autoconnect-priority", "100", timeout=5)

            # Double-check AP profile is gone
            nmcli("connection", "delete", AP_CONN_NAME, timeout=5)

            new_ip = _get_ip()
            logger.info(f"Background WiFi: New IP = {new_ip}")

            # Mark setup complete upon ACTUAL success
            config.set("setup_complete", True)

            # Bring remote access up. The supervisor retries on its own, so
            # this only waits long enough to put a usable address on the panel
            # and in the redirect; if the tunnel is slower than that, the
            # supervisor's callback repaints the screen when it lands.
            remote_access.start()
            public_url = _await_remote_url(timeout=20)

            redirect_url = public_url or f"http://{new_ip}:5000"
            real_ssid = get_active_ssid() or ssid
            try:
                display_mgr.display_wifi_connected(real_ssid, new_ip,
                                                   remote_url=public_url)
            except Exception as e:
                logger.error(f"Could not draw the connected screen: {e}")

            _wifi_connect_state.update({
                "status": "success", "ip": new_ip, "redirect_url": redirect_url,
                "remote_url": public_url})
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


# ── API: Remote access ───────────────────────────────────────────────────

@app.route('/api/remote')
def api_remote_status():
    """Where this device can be reached from outside the house."""
    state = remote_access.state()
    state["local_url"] = f"http://{_get_ip()}:5000"
    return jsonify(state)


@app.route('/api/remote/reconnect', methods=['POST'])
def api_remote_reconnect():
    """Force a fresh tunnel — the manual escape hatch when one is wedged."""
    remote_access.stop()
    remote_access.start()
    url = _await_remote_url(timeout=25)
    if url:
        return jsonify({"success": True, "url": url})
    state = remote_access.state()
    return jsonify({"error": state.get("error") or "Tunnel did not come up",
                    "status": state.get("status")}), 503


# ── API: System ──────────────────────────────────────────────────────────

@app.route('/api/status')
def api_status():
    return jsonify({
        "display": display_state,
        "photo": photo_state,
        "config": config.public_dict(),
        "total_images": len(get_image_list()),
        "system": get_system_info(),
    })


@app.route('/api/weather')
def api_weather():
    """Current weather for the configured place.

    `?refresh=1` skips the cache entirely. Settings' "Test" button uses it:
    the whole point of pressing Test after changing the city is to find out
    what *that* city says, and an answer from the previous one — which is what
    this used to return for the best part of an hour — is worse than no answer.
    """
    force = request.args.get("refresh") in ("1", "true", "yes")
    try:
        return jsonify(weather_svc.fetch_weather_strict(
            force=force, **weather_svc.params_from_config(config)))
    except WeatherError as exc:
        # A rejected key or an unknown city is something the owner fixes in
        # Settings; anything else is the upstream being unreachable, and
        # saying so beats handing back another city's reading.
        return jsonify({"error": str(exc)}), _upstream_status(exc)


@app.route('/api/calendar')
def api_calendar():
    """Get upcoming calendar events."""
    events = fetch_calendar_events(config.get("calendars", []))
    today = get_today_info()
    return jsonify({"today": today, "events": [
        {"summary": e.get("summary"),
         "start": e["start"].isoformat() if e.get("start") else None,
         "all_day": bool(e.get("all_day")),
         "calendar": e.get("calendar", ""),
         "color": e.get("color", "blue"),
         "event_color": e.get("event_color", "")}
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
    """The SSID this device is associated with right now, or None.

    Reads the link state rather than the scan list, so it is correct during
    the first minutes after a boot when no scan has completed yet.
    """
    device, state, profile = _wifi_device_status()
    if device is None:
        # NetworkManager did not answer at all — a transient D-Bus hiccup, or
        # a host that is not running it. Reporting "disconnected" here would
        # start the grace period on a link that is probably fine, so ask the
        # scan instead and accept its blind spot.
        try:
            check = nmcli("-t", "-f", "ACTIVE,SSID", "dev", "wifi", timeout=5)
            return _nmcli_active_ssid(check.stdout)
        except Exception:
            return None
    if state != "connected":
        return None
    return _profile_ssid(profile)

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
    """Pull the latest code and hand the log back to the browser.

    The script schedules its own restart in a detached unit, so this request
    finishes normally instead of being killed along with the service."""
    update_script = os.path.join(PROJECT_DIR, "scripts", "update.sh")
    if not os.path.exists(update_script):
        return jsonify({"error": "Update script not found"}), 500
    try:
        result = subprocess.run(
            ["bash", update_script],
            # pip on a Pi Zero is slow; 5 minutes was not always enough.
            capture_output=True, text=True, timeout=900, cwd=PROJECT_DIR)
        # git and pip both report progress on stderr, so keep the two streams
        # together — splitting them hid the reason for every failure.
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            logger.error(f"Update failed (exit {result.returncode})")
            return jsonify({"error": output.strip() or
                            f"Update failed (exit {result.returncode})"}), 500
        return jsonify({
            "success": True,
            "output": output,
            "restarting": "Restart scheduled" in output,
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


# How long NetworkManager gets to bring the saved network up on its own before
# we intervene, and how long the manual attempt then gets. Deadlines, not
# iteration counts: the old code counted 15 passes of a call that can block for
# seconds each, so a "15 second" wait was measured at four and a half minutes
# on real hardware — with the pairing hotspot raised at the end of it.
_BOOT_AUTOCONNECT_SECONDS = 45
_BOOT_MANUAL_SECONDS = 30


def _wait_for_ssid(ssid, seconds):
    """Poll until the device is on `ssid`, or the deadline passes."""
    deadline = time.time() + seconds
    while True:
        if get_active_ssid() == ssid:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(2)


def verify_or_connect_wifi_on_boot():
    ssid = config.get("wifi_ssid", "")
    password = config.get("wifi_password", "")

    if not ssid:
        return False

    logger.info(f"Boot check: verifying the connection to {ssid}...")

    # 1. NetworkManager has the profile and autoconnects on its own; usually
    #    this returns on the first pass.
    if _wait_for_ssid(ssid, _BOOT_AUTOCONNECT_SECONDS):
        logger.info(f"Boot check: on {ssid}.")
        return True

    # 2. Ask for it explicitly. Only worth doing once the link has demonstrably
    #    not come up by itself — it drops whatever wlan0 is doing.
    logger.warning(f"Boot check: not on {ssid} after "
                   f"{_BOOT_AUTOCONNECT_SECONDS}s — connecting explicitly.")
    args = ["dev", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    try:
        nmcli(*args, timeout=30)
    except subprocess.TimeoutExpired:
        pass

    if _wait_for_ssid(ssid, _BOOT_MANUAL_SECONDS):
        logger.info(f"Boot check: connected to {ssid}.")
        return True

    logger.error(f"Boot check: could not reach {ssid}.")
    return False


def resolve_boot_state():
    """Decide whether this boot lands in normal mode or the pairing hotspot.

    A boot is not a factory reset. Saved credentials — WiFi, the admin account,
    the session secret — are never touched here; only whether the device can
    currently reach its network is re-evaluated. Clearing them on every start
    is what forced re-pairing and re-registering after every power cut.

    Returns True when the device is on a network and can serve normally.
    """
    # Our own hotspot left up by a crash is not a network we joined.
    def _joined_network():
        ssid = get_active_ssid()
        return None if device_id.is_own_ap(ssid) else ssid

    if config.get("wifi_ssid"):
        if verify_or_connect_wifi_on_boot():
            logger.info("Boot check: reached the saved network.")
            config.set("setup_complete", True)
            return True

        # Not on the saved SSID — but possibly on another perfectly good one,
        # because the router's name changed or somebody re-pointed the device
        # with raspi-config. Raising the pairing hotspot means taking wlan0
        # into AP mode, so doing it here would knock a reachable device off
        # the air to advertise that it cannot be reached.
        live = _joined_network()
        if live:
            logger.warning(f"Boot check: {config.get('wifi_ssid')!r} not found, "
                           f"but the device is on {live!r} — adopting it.")
            config.update({"wifi_ssid": live, "setup_complete": True})
            return True

        # Keep the credentials. The router may simply be slower to come back
        # than we are; the hotspot lets the owner re-pair if the network really
        # did change, and a retry still has the password to try.
        if not config.get("setup_hotspot_fallback", True):
            logger.error("Boot check: saved WiFi unreachable. The pairing "
                         "hotspot is disabled on this device, so the watchdog "
                         "will keep retrying quietly.")
            config.set("setup_complete", True)
            return True

        logger.error("Boot check: saved WiFi unreachable — starting the setup hotspot.")
        config.set("setup_complete", False)
        return False

    adopted = _joined_network()
    if adopted:
        # Joined some other way (raspi-config, a pre-seeded wpa_supplicant), so
        # there is nothing to pair — adopt the network and carry on.
        logger.info(f"Boot check: already connected to {adopted}, adopting it.")
        config.update({"wifi_ssid": adopted, "setup_complete": True})
        return True

    # A device with no credentials at all has to pair, whatever the setting:
    # there is no saved network to retry and no other way in.
    logger.info("Boot check: no network configured — starting the setup hotspot.")
    config.set("setup_complete", False)
    return False


# Wire the services to their dependencies at import, not at start-up: the
# state they report (is remote access configured? which network are we
# watching?) has to be right the moment the first request arrives, including
# when this module is imported by a WSGI server rather than run directly.
# Only the threads below wait for an explicit start.
remote_access.init(config, on_url_change=_on_remote_url_change)
net_watchdog.init(
    config,
    active_ssid=get_active_ssid,
    start_ap=start_ap_hotspot,
    stop_ap=stop_ap_hotspot,
    scan_wifi=scan_and_cache_wifi,
    show_pairing_screen=display_mgr.display_qr_setup,
    connect_saved=verify_or_connect_wifi_on_boot,
    is_pairing_in_progress=lambda: _wifi_connect_state["status"] == "connecting",
)


def start_background_services():
    """Bring up the long-running workers. Safe to call once, at boot."""
    net_watchdog.start()

    # The slideshow flag is persisted, but nothing ever read it back, so a
    # power cut left the interface reporting a slideshow that was not running.
    if config.get("slideshow_active"):
        logger.info("Resuming slideshow from saved state")
        start_slideshow_thread()

    start_auto_refresh()

    if config.get("icloud_connected") and config.get("icloud_auto_sync", True):
        logger.info("Watching the connected iCloud album for new photos")
    start_icloud_sync()


def serve():
    """Run the HTTP server.

    Werkzeug's development server is single-threaded and explicitly not for
    production, which matters more here than usual: this process is reachable
    from the public internet through the tunnel. waitress is pure Python, so
    it installs on a Pi Zero without a compiler, and it is the default. The
    fallback exists so a checkout with missing dependencies still starts and
    can be fixed from the interface.
    """
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        logger.warning("waitress is not installed — falling back to the Flask "
                       "development server. Run: pip install -r requirements.txt")
        app.run(host='0.0.0.0', port=5000, debug=False)
        return

    # A Pi Zero 2 W has four slow cores; a handful of threads is enough to keep
    # the interface responsive while a panel refresh holds one for ~20 seconds.
    waitress_serve(app, host='0.0.0.0', port=5000, threads=8,
                   ident='Vignette', clear_untrusted_proxy_headers=False)


if __name__ == '__main__':
    resolve_boot_state()

    ip = _get_ip()
    print("=" * 60)
    print("  Vignette")
    print(f"  Output directory: {OUTPUT_DIR}")
    print(f"  Setup complete: {config.is_setup_complete}")
    print(f"  Local:   http://localhost:5000")
    print(f"  Network: http://{ip}:5000")
    if not config.is_setup_complete:
        print(f"  Pairing: join '{AP_SSID}' then open http://192.168.4.1:5000")
    print("=" * 60)

    start_background_services()

    # On first boot or failed reconnect: scan WiFi, then start AP
    if not config.is_setup_complete:
        logger.info("System in setup mode - scanning WiFi, starting AP, displaying QR setup")
        scan_and_cache_wifi()  # Scan BEFORE starting AP (wlan0 still in station mode)
        start_ap_hotspot()
        try:
            display_mgr.display_qr_setup(ip)
        except Exception as e:
            logger.error(f"Could not display QR setup: {e}")
    else:
        remote_access.start()
        public_url = _await_remote_url(timeout=25)
        if public_url:
            logger.info(f"Remote access ready: {public_url}")
        else:
            logger.warning("Remote access not up yet — the supervisor keeps "
                           "retrying; the panel updates when it lands.")
        try:
            ssid = get_active_ssid() or config.get("wifi_ssid", "WiFi")
            display_mgr.display_wifi_connected(ssid, ip, remote_url=public_url)
        except Exception as e:
            logger.error(f"Could not draw the connected screen: {e}")

    serve()
