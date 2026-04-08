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

    # Right: Photo (400x480)
    if photo_path and os.path.exists(photo_path):
        photo = _prepare_photo(photo_path, 396, 478,
                               config.get("photo_rotation", 0),
                               config.get("photo_fit_mode", "fit"))
        # Center photo in right panel
        px = 402 + (396 - photo.width) // 2
        py = 1 + (478 - photo.height) // 2
        img.paste(photo, (px, py))
    else:
        # No photo placeholder
        font = _get_font(20)
        draw.text((500, 220), "No Photo", fill=BLACK, font=font, anchor="mm")

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


def render_qr_setup(ip_address, port=5000, ap_ssid="Vignette-Setup", ap_password="vignette123"):
    """Render QR code WiFi setup page on e-paper (800x480).

    Layout (800x480):
      Row 1 (0-42):   Blue header "Vignette - WiFi Setup"
      Row 2 (48-68):  "Step 1" label  |  "Step 2" label
      Row 3 (70-310): WiFi QR code    |  URL QR code
      Row 4 (315-350): SSID/pass info |  URL info
      Row 5 (380-430): Instructions text
      Row 6 (440-480): Copyright
    """
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    wifi_qr_str = f"WIFI:T:WPA;S:{ap_ssid};P:{ap_password};;"
    url_str = f"http://192.168.4.1:{port}"

    font_title = _get_font(26)
    font_step = _get_font(20)
    font_label = _get_font(15)
    font_info = _get_font(14)
    font_small = _get_font(12)

    # ── Header bar ──
    draw.rectangle([0, 0, EPD_W, 42], fill=BLUE)
    draw.text((EPD_W // 2, 21), "Vignette - WiFi Setup",
              fill=WHITE, font=font_title, anchor="mm")

    # Layout constants
    qr_size = 240
    left_cx = 200   # center x of left half
    right_cx = 600  # center x of right half
    qr_top = 72

    try:
        import qrcode

        # ── Step labels ──
        draw.text((left_cx, 50), "Step 1: Connect WiFi",
                  fill=RED, font=font_step, anchor="mt")
        draw.text((right_cx, 50), "Step 2: Open Browser",
                  fill=RED, font=font_step, anchor="mt")

        # ── Left QR: WiFi ──
        qr1_x = left_cx - qr_size // 2
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(wifi_qr_str)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_img = qr_img.resize((qr_size, qr_size), _lanczos())
        img.paste(qr_img, (qr1_x, qr_top))

        # ── Right QR: URL ──
        qr2_x = right_cx - qr_size // 2
        qr2 = qrcode.QRCode(version=1, box_size=8, border=2)
        qr2.add_data(url_str)
        qr2.make(fit=True)
        qr2_img = qr2.make_image(fill_color="black", back_color="white").convert("RGB")
        qr2_img = qr2_img.resize((qr_size, qr_size), _lanczos())
        img.paste(qr2_img, (qr2_x, qr_top))

        # ── Info below QRs ──
        below_qr = qr_top + qr_size + 6
        draw.text((left_cx, below_qr),
                  f"WiFi: {ap_ssid}", fill=BLACK, font=font_info, anchor="mt")
        draw.text((left_cx, below_qr + 18),
                  f"Password: {ap_password}", fill=BLACK, font=font_small, anchor="mt")

        draw.text((right_cx, below_qr),
                  url_str, fill=BLUE, font=font_info, anchor="mt")
        draw.text((right_cx, below_qr + 18),
                  "Set up your home WiFi", fill=BLACK, font=font_small, anchor="mt")

    except ImportError:
        draw.text((EPD_W // 2, EPD_H // 2),
                  "QR Code\n(install qrcode module)", fill=RED, font=font_step, anchor="mm")

    # ── Arrow between QRs ──
    arrow_y = qr_top + qr_size // 2
    draw.text((EPD_W // 2, arrow_y), "->",
              fill=RED, font=_get_font(30), anchor="mm")

    # ── Bottom instructions ──
    inst_y = EPD_H - 60
    draw.text((EPD_W // 2, inst_y),
              f"Scan left QR to join WiFi \"{ap_ssid}\", then scan right QR to open setup page",
              fill=BLACK, font=font_small, anchor="mt")

    # ── Copyright ──
    draw.text((EPD_W // 2, EPD_H - 10),
              "\u00a9 2026 Teyra LLC W.Weng", fill=BLACK, font=_get_font(11), anchor="mb")

    return img


def render_wifi_connected(ssid, ip_address, port=5000):
    """Render 'WiFi Connected' confirmation page on e-paper.
    Shows the new IP address so the user knows where to access the web UI."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    url = f"http://{ip_address}:{port}"

    font_title = _get_font(36)
    font_big = _get_font(28)
    font_body = _get_font(22)
    font_info = _get_font(18)
    font_small = _get_font(14)

    # Success header
    draw.rectangle([0, 0, EPD_W, 60], fill=GREEN)
    draw.text((EPD_W // 2, 30), "WiFi Connected!", fill=WHITE, font=font_title, anchor="mm")

    # WiFi network name
    draw.text((EPD_W // 2, 100), f"Network: {ssid}", fill=BLACK, font=font_body, anchor="mt")

    # Big URL - this is the most important info
    draw.text((EPD_W // 2, 160), "Open in browser:", fill=BLACK, font=font_info, anchor="mt")
    draw.text((EPD_W // 2, 200), url, fill=BLUE, font=font_big, anchor="mt")

    # QR code for the URL
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=5, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_x = (EPD_W - qr_img.width) // 2
        img.paste(qr_img, (qr_x, 250))
        qr_bottom = 250 + qr_img.height + 10
    except ImportError:
        qr_bottom = 260

    # Instructions
    draw.text((EPD_W // 2, max(qr_bottom, 420)),
              "Connect your phone to the same WiFi, then scan QR or type the URL",
              fill=BLACK, font=font_small, anchor="mt")

    draw.text((EPD_W // 2, EPD_H - 18),
              "\u00a9 2026 Teyra LLC W.Weng", fill=BLACK, font=font_small, anchor="mb")

    return img


def render_otp_page(code):
    """Render the 6-digit OTP code on the e-paper for Hardware 2FA."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    font_title = _get_font(42)
    font_code = _get_font(120)
    font_desc = _get_font(24)
    font_small = _get_font(16)

    # Header
    draw.rectangle([0, 0, EPD_W, 70], fill=RED)
    draw.text((EPD_W // 2, 35), "Authentication Required", fill=WHITE, font=font_title, anchor="mm")

    # Code
    draw.text((EPD_W // 2, EPD_H // 2 - 20), code, fill=BLACK, font=font_code, anchor="mm")

    # Description
    draw.text((EPD_W // 2, EPD_H // 2 + 80), "Enter this 6-digit code on the web interface.", fill=BLACK, font=font_desc, anchor="mm")
    
    # Expiry warning
    draw.text((EPD_W // 2, EPD_H // 2 + 130), "This code will expire in 10 minutes.", fill=RED, font=font_small, anchor="mm")

    draw.text((EPD_W // 2, EPD_H - 20),
              "\u00a9 2026 Teyra LLC W.Weng", fill=BLACK, font=font_small, anchor="mb")

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
