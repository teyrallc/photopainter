"""
E-paper page renderer.
Draws the three page views as PIL Images (800x480, 6-color).

Pages:
  - Home: Calendar (left-top 400x240) + Weather (left-bottom 400x240) + Photo (right 400x480)
  - Widget: Full-screen calendar or weather (800x480)
  - Photo: Full-screen photo (800x480) with rotation & fit mode
"""

import logging
import os
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("vignette.renderer")

EPD_W = 800
EPD_H = 480

# Colors (6-color e-paper: black, white, yellow, red, blue, green)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)

# Font paths to try (CJK fonts first for Chinese support)
FONT_PATHS = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

_font_cache = {}

# Amsterdam Three logo font (same file used by the SVG logo)
_LOGO_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "img", "Amsterdam_Three.ttf"
)
_logo_font_cache = {}


def _get_logo_font(size):
    """Get Amsterdam Three font for the Vignette logo wordmark."""
    if size in _logo_font_cache:
        return _logo_font_cache[size]
    try:
        font = ImageFont.truetype(_LOGO_FONT_PATH, size)
        _logo_font_cache[size] = font
        return font
    except Exception:
        return _get_font(size)  # graceful fallback


def _draw_logo(draw, cx, cy, size, fill):
    """Draw 'Vignette' wordmark centered at (cx, cy)."""
    draw.text((cx, cy), "Vignette", fill=fill, font=_get_logo_font(size), anchor="mm")


def _get_font(size):
    """Get a font at the given size, trying CJK fonts first for Chinese support."""
    if size in _font_cache:
        return _font_cache[size]

    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                # TrueType Collections (.ttc) need font index 0
                font = ImageFont.truetype(path, size, index=0)
                _font_cache[size] = font
                return font
            except Exception:
                continue

    font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def render_home_page(weather_data, calendar_events, photo_path, config):
    """Render the Home page: Calendar + Weather + Photo."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    # Left: Calendar (top 400x240)
    _draw_calendar_panel(draw, 0, 0, 400, 240, calendar_events)

    # Left: Weather (bottom 400x240)
    _draw_weather_panel(draw, 0, 240, 400, 240, weather_data)

    # Divider line
    draw.line([(400, 0), (400, EPD_H)], fill=BLACK, width=2)
    draw.line([(0, 240), (400, 240)], fill=BLACK, width=1)

    # Right: Photo panel (x=402–798, 396×480)
    PANEL_CX = 402 + 396 // 2   # 600
    PANEL_CY = EPD_H // 2       # 240

    if photo_path and os.path.exists(photo_path):
        photo = _prepare_photo(photo_path, 396, 478,
                               config.get("photo_rotation", 0),
                               config.get("photo_fit_mode", "fit"))
        px = 402 + (396 - photo.width) // 2
        py = 1 + (478 - photo.height) // 2
        img.paste(photo, (px, py))
    else:
        # Default: Vignette logo as placeholder
        _draw_logo(draw, PANEL_CX, PANEL_CY - 20, 82, BLACK)
        draw.text((PANEL_CX, PANEL_CY + 62),
                  "Smart Display", fill=BLACK, font=_get_font(14), anchor="mt")

    return img


def render_widget_page(mode, weather_data, calendar_events):
    """Render full-screen widget: weather, calendar, or split (both).
    Split mode: left=calendar, right=weather, each 400x480."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    if mode == "split":
        _draw_calendar_panel(draw, 0, 0, 400, EPD_H, calendar_events)
        _draw_weather_panel(draw, 400, 0, 400, EPD_H, weather_data)
        draw.line([(400, 0), (400, EPD_H)], fill=BLACK, width=2)
    elif mode == "weather":
        _draw_weather_fullscreen(draw, weather_data)
    else:
        _draw_calendar_fullscreen(draw, calendar_events)

    return img


def render_photo_page(photo_path, rotation=0, fit_mode="fit"):
    """Render full-screen photo with rotation and fit mode."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)

    if photo_path and os.path.exists(photo_path):
        photo = _prepare_photo(photo_path, EPD_W, EPD_H, rotation, fit_mode)
        px = (EPD_W - photo.width) // 2
        py = (EPD_H - photo.height) // 2
        img.paste(photo, (px, py))
    else:
        draw = ImageDraw.Draw(img)
        font = _get_font(28)
        draw.text((EPD_W // 2, EPD_H // 2), "No Photo", fill=BLACK,
                  font=font, anchor="mm")

    return img


def render_qr_setup(ip_address=None, port=5000, ap_ssid="Vignette", ap_password=""):
    """Render QR code WiFi setup page on e-paper (800x480).
    Modern two-column layout: WiFi QR | URL QR.

    `ip_address` is accepted for call-site symmetry but deliberately unused:
    this page is only ever shown while the device is serving its own hotspot,
    where the only address that works is the fixed gateway below.
    """
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    wifi_qr_str = f"WIFI:T:WPA;S:{ap_ssid};P:{ap_password};;"
    url_str = f"http://192.168.4.1:{port}"

    font_header = _get_font(22)
    font_step   = _get_font(15)
    font_info   = _get_font(14)
    font_small  = _get_font(12)
    font_tiny   = _get_font(10)

    # ── Header (0–60): logo left · divider · "WiFi Setup" right ──
    draw.rectangle([0, 0, EPD_W, 60], fill=BLUE)
    _draw_logo(draw, EPD_W // 2 - 80, 30, 38, WHITE)
    draw.line([(EPD_W // 2 - 8, 14), (EPD_W // 2 - 8, 46)],
              fill=(180, 180, 220), width=1)
    draw.text((EPD_W // 2 + 52, 30), "WiFi Setup",
              fill=WHITE, font=font_header, anchor="mm")

    # ── Column centers ──
    L = EPD_W // 4        # 200
    R = 3 * EPD_W // 4   # 600
    QR_SIZE = 210
    QR_TOP  = 88

    # Vertical divider between columns
    draw.line([(EPD_W // 2, 64), (EPD_W // 2, EPD_H - 80)],
              fill=(180, 180, 180), width=1)

    # ── Step indicators (64–86) ──
    for i, (cx, label) in enumerate([(L, "Connect to WiFi"), (R, "Open Browser")], 1):
        draw.ellipse([cx - 36, 68, cx - 16, 84], fill=BLUE)
        draw.text((cx - 26, 76), str(i), fill=WHITE, font=font_tiny, anchor="mm")
        draw.text((cx - 6,  76), label, fill=BLACK, font=font_step, anchor="lm")

    try:
        import qrcode

        # Left QR — WiFi credentials
        qr = qrcode.QRCode(box_size=7, border=2)
        qr.add_data(wifi_qr_str)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_img = qr_img.resize((QR_SIZE, QR_SIZE), _lanczos())
        img.paste(qr_img, (L - QR_SIZE // 2, QR_TOP))

        # Right QR — setup URL
        qr2 = qrcode.QRCode(box_size=7, border=2)
        qr2.add_data(url_str)
        qr2.make(fit=True)
        qr2_img = qr2.make_image(fill_color="black", back_color="white").convert("RGB")
        qr2_img = qr2_img.resize((QR_SIZE, QR_SIZE), _lanczos())
        img.paste(qr2_img, (R - QR_SIZE // 2, QR_TOP))

        # Info below QRs
        info_y = QR_TOP + QR_SIZE + 8   # 306
        draw.text((L, info_y),      ap_ssid,     fill=BLACK, font=font_info,  anchor="mt")
        draw.text((L, info_y + 17), ap_password, fill=BLACK, font=font_small, anchor="mt")
        draw.text((R, info_y),      url_str,     fill=BLUE,  font=font_info,  anchor="mt")

    except ImportError:
        draw.text((EPD_W // 2, 240),
                  "Install qrcode module", fill=RED, font=font_header, anchor="mm")

    # ── Footer ──
    FT = EPD_H - 80   # 400
    draw.line([(20, FT), (EPD_W - 20, FT)], fill=(180, 180, 180), width=1)
    draw.text((EPD_W // 2, FT + 6),
              f'\u2460 Scan left QR  \u2192  Join "{ap_ssid}"'
              f'     \u2461 Scan right QR  \u2192  Open browser',
              fill=BLACK, font=font_tiny, anchor="mt")
    _draw_logo(draw, EPD_W // 2, FT + 38, 18, BLACK)
    draw.text((EPD_W // 2, FT + 60),
              "\u00a9 2026 Vignette", fill=BLACK, font=font_tiny, anchor="mt")

    return img


def render_wifi_connected(ssid, ip_address, port=5000, remote_url=None):
    """Render 'WiFi Connected' confirmation page on e-paper (800x480).
    Clean centered layout: header · network · URL · QR · footer.

    When a tunnel is up, `remote_url` is what goes on the panel and into the
    QR: the LAN address only works from inside the house, and this screen is
    the one moment the owner is standing there ready to scan it. The LAN
    address is still printed underneath as the local fallback.
    """
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    local_url = f"http://{ip_address}:{port}"
    if remote_url:
        url = remote_url
    elif "ngrok" in str(ip_address):
        # Older callers passed a bare tunnel hostname in place of the IP.
        url = f"https://{ip_address}"
    else:
        url = local_url

    font_title = _get_font(34)
    font_label = _get_font(18)
    font_small = _get_font(13)
    font_tiny  = _get_font(10)

    # ── Header (0–58) ──
    draw.rectangle([0, 0, EPD_W, 58], fill=GREEN)
    draw.text((EPD_W // 2, 29), "WiFi Connected!",
              fill=WHITE, font=font_title, anchor="mm")

    # ── Network name ──
    draw.text((EPD_W // 2, 72), f"Network:  {ssid}",
              fill=BLACK, font=font_label, anchor="mt")

    # ── URL (adaptive font: shorter URL = larger text) ──
    url_fs = 22 if len(url) <= 35 else 18 if len(url) <= 50 else 13
    draw.text((EPD_W // 2, 100), "Open in your browser:",
              fill=BLACK, font=font_small, anchor="mt")
    draw.text((EPD_W // 2, 120), url,
              fill=BLUE, font=_get_font(url_fs), anchor="mt")

    # ── QR code (fixed 210 px, centered) ──
    QR_SIZE = 210
    QR_TOP  = 150
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_img = qr_img.resize((QR_SIZE, QR_SIZE), _lanczos())
        img.paste(qr_img, ((EPD_W - QR_SIZE) // 2, QR_TOP))
    except ImportError:
        pass

    # ── Instructions ──
    instructions = ("Scan the QR code or type the URL in your browser"
                    if not remote_url else
                    "Scan the QR code — this address works from anywhere")
    draw.text((EPD_W // 2, QR_TOP + QR_SIZE + 10),
              instructions, fill=BLACK, font=font_tiny, anchor="mt")

    # When the tunnel is what the QR points at, the LAN address is still worth
    # printing: it keeps working if the internet drops.
    if remote_url:
        draw.text((EPD_W // 2, QR_TOP + QR_SIZE + 26),
                  f"On this network: {local_url}",
                  fill=BLACK, font=font_tiny, anchor="mt")

    # ── Footer ──
    FT = EPD_H - 74   # 406
    draw.line([(20, FT), (EPD_W - 20, FT)], fill=(180, 180, 180), width=1)
    _draw_logo(draw, EPD_W // 2, FT + 28, 20, BLACK)
    draw.text((EPD_W // 2, FT + 52),
              "\u00a9 2026 Vignette", fill=BLACK, font=font_tiny, anchor="mt")

    return img


def render_otp_page(code):
    """Render the 6-digit OTP code on the e-paper for Hardware 2FA."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    font_title = _get_font(38)
    font_code  = _get_font(100)
    font_desc  = _get_font(22)
    font_small = _get_font(15)
    font_tiny  = _get_font(10)

    # ── Header (0–65) ──
    draw.rectangle([0, 0, EPD_W, 65], fill=RED)
    draw.text((EPD_W // 2, 32), "Authentication Required",
              fill=WHITE, font=font_title, anchor="mm")

    # ── Sub-label ──
    draw.text((EPD_W // 2, 82), "Hardware Verification Code",
              fill=BLACK, font=_get_font(16), anchor="mt")

    # ── OTP Code ──
    draw.text((EPD_W // 2, 228), code,
              fill=BLACK, font=font_code, anchor="mm")

    # ── Description + expiry ──
    draw.text((EPD_W // 2, 320), "Enter this code on the web interface.",
              fill=BLACK, font=font_desc, anchor="mt")
    draw.text((EPD_W // 2, 356), "Expires in 10 minutes.",
              fill=RED, font=font_small, anchor="mt")

    # ── Footer (consistent with other pages) ──
    FT = EPD_H - 74   # 406
    draw.line([(20, FT), (EPD_W - 20, FT)], fill=(180, 180, 180), width=1)
    _draw_logo(draw, EPD_W // 2, FT + 28, 20, BLACK)
    draw.text((EPD_W // 2, FT + 52),
              "\u00a9 2026 Vignette", fill=BLACK, font=font_tiny, anchor="mt")

    return img


# ── Internal Drawing Functions ────────────────────────────────────────────


def _prepare_photo(path, max_w, max_h, rotation=0, fit_mode="fit"):
    """Load, rotate, and resize a photo."""
    photo = Image.open(path).convert("RGB")

    if rotation:
        photo = photo.rotate(-rotation, expand=True)

    if fit_mode == "stretch":
        photo = photo.resize((max_w, max_h), _lanczos())
    else:
        # Fit: maintain aspect ratio, letterbox with white
        photo.thumbnail((max_w, max_h), _lanczos())

    return photo


def _lanczos():
    return getattr(Image, 'Resampling', Image).LANCZOS


def _draw_calendar_panel(draw, x, y, w, h, events):
    """Draw calendar in a panel region."""
    now = datetime.now()
    weekdays_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    font_date = _get_font(36)
    font_day = _get_font(18)
    font_event = _get_font(14)

    # Date header (English)
    date_str = now.strftime("%b %d")
    day_str = weekdays_en[now.weekday()]
    draw.text((x + w // 2, y + 20), date_str, fill=BLACK, font=font_date, anchor="mt")
    draw.text((x + w // 2, y + 62), day_str, fill=RED if now.weekday() >= 5 else BLUE,
              font=font_day, anchor="mt")

    # Mini month calendar
    _draw_mini_calendar(draw, x + 20, y + 90, w - 40, now)

    # Upcoming events (if any)
    ey = y + h - 10
    if events:
        for ev in events[:2]:
            start = ev.get("start")
            summary = ev.get("summary", "?")[:20]
            if start:
                time_str = start.strftime("%m/%d %H:%M")
                draw.text((x + 10, ey - 20), f"• {time_str} {summary}",
                          fill=BLACK, font=font_event, anchor="lb")
                ey -= 18


def _draw_mini_calendar(draw, x, y, w, now):
    """Draw a small monthly calendar grid."""
    font = _get_font(12)

    import calendar
    cal = calendar.monthcalendar(now.year, now.month)
    cell_w = w // 7
    cell_h = 16

    # Weekday headers
    headers = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    for i, hdr in enumerate(headers):
        cx = x + i * cell_w + cell_w // 2
        color = RED if i >= 5 else BLACK
        draw.text((cx, y), hdr, fill=color, font=font, anchor="mt")

    # Day numbers
    for row_idx, week in enumerate(cal):
        for col_idx, day in enumerate(week):
            if day == 0:
                continue
            cx = x + col_idx * cell_w + cell_w // 2
            cy = y + (row_idx + 1) * cell_h + 4

            if day == now.day:
                # Highlight today
                r = 8
                draw.ellipse([cx - r, cy - r + 2, cx + r, cy + r + 2], fill=RED)
                draw.text((cx, cy + 2), str(day), fill=WHITE, font=font, anchor="mm")
            else:
                color = RED if col_idx >= 5 else BLACK
                draw.text((cx, cy + 2), str(day), fill=color, font=font, anchor="mm")


def _draw_weather_panel(draw, x, y, w, h, weather):
    """Draw weather in a panel region."""
    font_temp = _get_font(48)
    font_desc = _get_font(18)
    font_detail = _get_font(14)

    if not weather:
        draw.text((x + w // 2, y + h // 2), "No weather data",
                  fill=BLACK, font=font_desc, anchor="mm")
        draw.text((x + w // 2, y + h // 2 + 25), "Configure in Settings",
                  fill=BLUE, font=font_detail, anchor="mm")
        return

    # Temperature
    temp_str = f"{weather['temp']}°"
    draw.text((x + w // 2, y + 20), temp_str, fill=BLACK, font=font_temp, anchor="mt")

    # Description
    desc = weather.get("description", "")
    draw.text((x + w // 2, y + 80), desc, fill=BLACK, font=font_desc, anchor="mt")

    # Details row
    details = f"H:{weather['temp_max']}°  L:{weather['temp_min']}°  Hum:{weather['humidity']}%"
    draw.text((x + w // 2, y + 108), details, fill=BLACK, font=font_detail, anchor="mt")

    # Forecast
    fy = y + 140
    font_fc = _get_font(13)
    for fc in weather.get("forecast", [])[:3]:
        line = f"{fc['date']}({fc['weekday']}) {fc['description']}  {fc['temp_min']}~{fc['temp_max']}°"
        draw.text((x + 15, fy), line, fill=BLACK, font=font_fc)
        fy += 20

    # Updated time
    draw.text((x + w - 10, y + h - 10),
              f"Updated: {weather.get('updated', '?')}",
              fill=BLACK, font=_get_font(11), anchor="rb")


def _draw_weather_fullscreen(draw, weather):
    """Draw full-screen weather view."""
    font_city = _get_font(28)
    font_temp = _get_font(80)
    font_desc = _get_font(28)
    font_detail = _get_font(20)
    font_fc = _get_font(18)

    if not weather:
        draw.text((EPD_W // 2, EPD_H // 2), "No weather data",
                  fill=BLACK, font=font_desc, anchor="mm")
        draw.text((EPD_W // 2, EPD_H // 2 + 40), "Please configure in Settings",
                  fill=BLUE, font=font_detail, anchor="mm")
        return

    # Header bar
    draw.rectangle([0, 0, EPD_W, 50], fill=BLUE)
    draw.text((EPD_W // 2, 25), f"Weather - {weather['city']}",
              fill=WHITE, font=font_city, anchor="mm")

    # Current temperature (big)
    draw.text((EPD_W // 2, 120), f"{weather['temp']}°",
              fill=BLACK, font=font_temp, anchor="mt")

    # Description
    draw.text((EPD_W // 2, 210), weather.get("description", ""),
              fill=BLACK, font=font_desc, anchor="mt")

    # Details
    detail_y = 255
    details = [
        f"Feels like: {weather['feels_like']}°",
        f"H: {weather['temp_max']}°  L: {weather['temp_min']}°",
        f"Humidity: {weather['humidity']}%",
        f"Wind: {weather['wind_speed']} m/s",
    ]
    for d in details:
        draw.text((EPD_W // 2, detail_y), d, fill=BLACK, font=font_detail, anchor="mt")
        detail_y += 28

    # Forecast section
    draw.line([(50, 375), (EPD_W - 50, 375)], fill=BLACK, width=1)
    fx = 100
    for fc in weather.get("forecast", [])[:3]:
        draw.text((fx, 390), f"{fc['date']}", fill=BLACK, font=font_detail, anchor="mt")
        draw.text((fx, 415), f"({fc['weekday']})", fill=BLACK, font=_get_font(14), anchor="mt")
        draw.text((fx, 435), f"{fc['temp_min']}~{fc['temp_max']}°",
                  fill=BLACK, font=font_detail, anchor="mt")
        draw.text((fx, 458), fc["description"][:4],
                  fill=BLACK, font=_get_font(14), anchor="mt")
        fx += 250

    draw.text((EPD_W - 10, EPD_H - 10),
              f"Updated: {weather.get('updated', '?')}",
              fill=BLACK, font=_get_font(12), anchor="rb")


def _draw_calendar_fullscreen(draw, events):
    """Draw full-screen calendar view."""
    now = datetime.now()
    weekdays_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    font_header = _get_font(28)
    font_date = _get_font(48)
    font_event = _get_font(18)
    font_day = _get_font(16)

    # Header bar
    draw.rectangle([0, 0, EPD_W, 50], fill=RED)
    month_str = now.strftime("%B %Y")
    draw.text((EPD_W // 2, 25), month_str, fill=WHITE, font=font_header, anchor="mm")

    # Today's date (big)
    date_str = f"{now.day}"
    day_str = weekdays_en[now.weekday()]
    draw.text((EPD_W // 2, 90), date_str, fill=BLACK, font=_get_font(64), anchor="mt")
    draw.text((EPD_W // 2, 160), day_str, fill=RED if now.weekday() >= 5 else BLUE,
              font=font_header, anchor="mt")

    # Monthly calendar grid
    import calendar
    cal = calendar.monthcalendar(now.year, now.month)
    grid_x, grid_y = 80, 200
    cell_w = (EPD_W - 160) // 7
    cell_h = 28

    headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i, hdr in enumerate(headers):
        cx = grid_x + i * cell_w + cell_w // 2
        color = RED if i >= 5 else BLACK
        draw.text((cx, grid_y), hdr, fill=color, font=font_day, anchor="mt")

    for row_idx, week in enumerate(cal):
        for col_idx, day in enumerate(week):
            if day == 0:
                continue
            cx = grid_x + col_idx * cell_w + cell_w // 2
            cy = grid_y + (row_idx + 1) * cell_h + 8

            if day == now.day:
                r = 13
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED)
                draw.text((cx, cy), str(day), fill=WHITE, font=font_day, anchor="mm")
            else:
                color = RED if col_idx >= 5 else BLACK
                draw.text((cx, cy), str(day), fill=color, font=font_day, anchor="mm")

    # Events list
    ey = grid_y + (len(cal) + 1) * cell_h + 20
    draw.line([(50, ey - 5), (EPD_W - 50, ey - 5)], fill=BLACK, width=1)

    if events:
        for ev in events[:4]:
            start = ev.get("start")
            summary = ev.get("summary", "?")[:30]
            if start:
                ts = start.strftime("%m/%d %H:%M")
                draw.text((80, ey), f"• {ts}  {summary}", fill=BLACK, font=font_event)
                ey += 24
    else:
        draw.text((EPD_W // 2, ey + 5), "No upcoming events",
                  fill=BLACK, font=font_event, anchor="mt")
