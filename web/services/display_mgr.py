import logging
import threading
import os
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from services import renderer
from services import epd as epd_service
from services import device_id
from services import weather as weather_svc
from services.calendar_svc import fetch_calendar_events

# Amsterdam Three logo font path
_LOGO_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "img", "Amsterdam_Three.ttf"
)
_logo_font = None

def _get_logo_font(size):
    try:
        return ImageFont.truetype(_LOGO_FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()

logger = logging.getLogger("vignette.display")
display_lock = threading.RLock()

# Shared state (initialized by app)
display_state = {
    "current_image": None,
    "last_update": None,
    "status": "idle",
}

config = None
photo_state = None
get_current_photo_path_func = None

# Constants (mirrored from app or passed in)
EPD_WIDTH = 800
EPD_HEIGHT = 480
LANCZOS = getattr(Image, 'Resampling', Image).LANCZOS

def init_display_mgr(cfg, p_state, get_photo_path_fn):
    global config, photo_state, get_current_photo_path_func
    config = cfg
    photo_state = p_state
    get_current_photo_path_func = get_photo_path_fn

VALID_ROTATIONS = (0, 90, 180, 270)


def display_rotation():
    """How far the finished page is turned before it reaches the panel."""
    value = config.get("display_rotation", 180) if config else 180
    try:
        value = int(value) % 360
    except (TypeError, ValueError):
        return 180
    return value if value in VALID_ROTATIONS else 180


def is_portrait():
    """Whether pages are composed tall. 90 and 270 hang the frame on its side."""
    return display_rotation() in (90, 270)


def orient_for_panel(img, degrees=None):
    """Turn a composed page into the panel's own 800x480 buffer.

    Image.rotate(expand=True) turns anticlockwise, which is what the setting
    means: 270 is the same as -90, the angle a frame hung with its top edge to
    the left needs. A portrait page is 480x800 and comes out 800x480; a
    landscape one is already the right shape and 180 keeps it that way.

    Anything that does not land on the panel's exact size is centred on it
    rather than stretched — a mismatch means the config and the layout have
    disagreed, and a letterboxed page is far easier to diagnose than a
    squashed one.
    """
    degrees = display_rotation() if degrees is None else degrees
    if degrees:
        img = img.rotate(degrees, expand=True)
    if img.size == (EPD_WIDTH, EPD_HEIGHT):
        return img
    logger.warning(f"Composed page is {img.size}, not {(EPD_WIDTH, EPD_HEIGHT)}; "
                   "centring it on the panel")
    canvas = Image.new("RGB", (EPD_WIDTH, EPD_HEIGHT), (255, 255, 255))
    fitted = img.copy()
    fitted.thumbnail((EPD_WIDTH, EPD_HEIGHT), LANCZOS)
    canvas.paste(fitted, ((EPD_WIDTH - fitted.width) // 2,
                          (EPD_HEIGHT - fitted.height) // 2))
    return canvas


def display_pil_image(img):
    """Send a PIL Image to the e-paper display with thread safety and retry logic."""
    with display_lock:
        display_state["status"] = "displaying"
        logger.info("Sending image to e-paper...")
        img = orient_for_panel(img)
        
        # Try up to 3 times to initialize and display
        last_error = None
        for attempt in range(3):
            try:
                epd = epd_service.get_epd(config.get("epd_model") if config else None)

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

def display_current_page():
    """Render and display the current page view on e-paper."""
    page = config.get("current_page", "photo")
    logger.info(f"Rendering page: {page}")

    # Only the pages that show them, and only fresh. A repaint is the moment
    # what stands on the wall for the next hour gets decided, so the calendar
    # is read live rather than from the quarter-hour cache the browser uses.
    # The photo page needs neither, and asking for them there had a five-minute
    # slideshow pulling somebody's calendar feed twelve times an hour to draw
    # a picture with no calendar on it.
    events = []
    weather = None
    if page in ("home", "widget"):
        weather = weather_svc.fetch_for_config(config)
        if config.get("calendars"):
            events = fetch_calendar_events(config.get("calendars"), refresh=True)

    photo_path = get_current_photo_path_func()

    portrait = is_portrait()
    if page == "home":
        img = renderer.render_home_page(weather, events, photo_path, config,
                                        portrait=portrait)
    elif page == "widget":
        mode = config.get("widget_mode", "weather")
        img = renderer.render_widget_page(mode, weather, events, portrait=portrait)
    else:  # photo
        rotation = config.get("photo_rotation", 0)
        fit_mode = config.get("photo_fit_mode", "fit")
        img = renderer.render_photo_page(photo_path, rotation, fit_mode,
                                         portrait=portrait)

    display_state["current_image"] = f"[{page} page]"
    return display_pil_image(img)

def display_qr_setup(ip=None):
    """Display QR code setup page on e-paper.

    `ip` is optional because the page it renders always points at the hotspot
    gateway, never at the device's LAN address — two of the three callers had
    nothing sensible to pass and were crashing on the missing argument.

    The hotspot name and password come from this device rather than from
    constants, and this screen is the only place that password is published.
    """
    ssid, password = device_id.ap_credentials(config)
    img = renderer.render_qr_setup(ip, ap_ssid=ssid, ap_password=password,
                                   portrait=is_portrait())
    display_state["current_image"] = "[QR setup]"
    return display_pil_image(img)

def display_wifi_connected(ssid, ip_address, remote_url=None):
    """Display the 'connected' screen.

    `remote_url` is the tunnel address when one is up — that is the one the
    owner actually needs, since the LAN address only works inside the house.
    """
    img = renderer.render_wifi_connected(ssid, ip_address, remote_url=remote_url,
                                         portrait=is_portrait())
    display_state["current_image"] = "[WiFi connected]"
    return display_pil_image(img)

def display_otp_code(code):
    """Display 6-digit Hardware Auth OTP on e-paper."""
    img = renderer.render_otp_page(code, portrait=is_portrait())
    display_state["current_image"] = "[OTP Code]"
    return display_pil_image(img)

def display_test_pattern():
    """Send a test pattern to e-paper."""
    logger.info("Sending test pattern...")
    # Composed the way the frame is hung, so the bars fill the panel in either
    # orientation instead of being letterboxed into the middle of it.
    width, height = renderer.page_size(is_portrait())
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    colors = [
        ((0, 0, 0), "Black"), ((255, 255, 255), "White"),
        ((0, 255, 0), "Green"), ((0, 0, 255), "Blue"),
        ((255, 0, 0), "Red"), ((255, 255, 0), "Yellow"),
    ]
    bar_width = width // len(colors)
    for i, (color, name) in enumerate(colors):
        x0, x1 = i * bar_width, (i + 1) * bar_width
        draw.rectangle([x0, 0, x1, height], fill=color)
        tc = (255, 255, 255) if color in [(0, 0, 0), (0, 0, 255)] else (0, 0, 0)
        draw.text((x0 + 10, height // 2), name, fill=tc)
    draw.rectangle([0, 0, width, 48], fill=(0, 0, 0))
    draw.text((12, 24), "Vignette", fill=(255, 255, 255),
              font=_get_logo_font(34), anchor="lm")
    draw.text((min(240, width - 200), 24), "— E-Paper Test Pattern",
              fill=(255, 255, 255), font=ImageFont.load_default(), anchor="lm")
    display_state["current_image"] = "[test pattern]"
    return display_pil_image(img)

def display_image_on_epaper(image_path):
    """Display an image file on e-paper."""
    display_state["current_image"] = os.path.basename(image_path)
    img = Image.open(image_path).convert("RGB")
    img = img.resize((EPD_WIDTH, EPD_HEIGHT), LANCZOS)
    return display_pil_image(img)
