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

from services import epd_ui as ui

logger = logging.getLogger("vignette.renderer")

EPD_W = 800
EPD_H = 480

# The six the panel can render. Kept as names here for the pages written
# before services/epd_ui.py existed; both refer to the same palette.
BLACK = ui.BLACK
WHITE = ui.WHITE
RED = ui.RED
YELLOW = ui.YELLOW
BLUE = ui.BLUE
GREEN = ui.GREEN

FONT_PATHS = ui.FONT_PATHS

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
    """Get a font at the given size. One cache, shared with services/epd_ui."""
    return ui.font(size)


def render_home_page(weather_data, calendar_events, photo_path, config):
    """Render the Home page: Calendar + Weather + Photo."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)
    lang = config.get("lang", "en") if config else "en"

    # Left: Calendar (top 400x240)
    _draw_calendar_panel(draw, 0, 0, 400, 240, calendar_events, lang)

    # Left: Weather (bottom 400x240)
    _draw_weather_panel(draw, 0, 240, 400, 240, weather_data, lang)

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


def render_widget_page(mode, weather_data, calendar_events, lang="en"):
    """Render full-screen widget: weather, calendar, or split (both)."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    if mode == "split":
        _draw_split_page(draw, weather_data, calendar_events, lang)
    elif mode == "weather":
        _draw_weather_fullscreen(draw, weather_data, lang)
    else:
        _draw_calendar_fullscreen(draw, calendar_events, lang)

    return img


def _draw_split_page(draw, weather, events, lang="en"):
    """Calendar and weather side by side, each given a full-height column.

    Not the quarter-panel layouts stretched: those are designed for 240 pixels
    of height, and simply handing them 480 left a band of white through the
    middle of both halves. Each column is composed for the space it has.
    """
    now = datetime.now()
    chinese = ui.use_chinese(lang)
    left = ui.Box(0, 0, 400, EPD_H)
    right = ui.Box(400, 0, 400, EPD_H)
    draw.line([(400, 0), (400, EPD_H)], fill=BLACK, width=2)

    # ── Left: the month, then what is on ──
    head, rest = left.cut_top(46)
    ui.header(draw, head, ui.month_label(now, lang), fill=RED, title_size=21)

    rest = rest.inset(14, 12)
    today_box, below = rest.cut_top(72, gap=8)
    day_font = ui.font(60)
    draw.text((today_box.x, today_box.y - 6), str(now.day),
              fill=BLACK, font=day_font, anchor="lt")
    offset = ui.text_width(str(now.day), day_font) + 12
    draw.text((today_box.x + offset, today_box.y + 8),
              ui.weekday_label(now.weekday(), lang, short=False),
              fill=RED if now.weekday() >= 5 else BLUE, font=ui.font(20), anchor="lt")
    draw.text((today_box.x + offset, today_box.y + 36),
              ui.date_label(now, lang), fill=BLACK, font=ui.font(15), anchor="lt")

    grid_box, agenda_box = below.cut_top(int(below.h * 0.52), gap=10)
    _draw_month_grid(draw, grid_box, now, events, lang)

    label_box, list_box = agenda_box.cut_top(20, gap=2)
    draw.text((label_box.x, label_box.y), "接下來" if chinese else "Coming up",
              fill=BLUE, font=ui.font(14), anchor="lt")
    _draw_agenda(draw, list_box, events, lang, limit=3)

    # ── Right: now, then the next three days ──
    if not weather:
        head_r, rest_r = right.cut_top(46)
        ui.header(draw, head_r, "天氣" if chinese else "Weather",
                  fill=BLUE, title_size=21)
        ui.empty_state(draw, rest_r,
                       "尚未設定天氣" if chinese else "No weather data",
                       "Settings → Weather")
        return

    head_r, rest_r = right.cut_top(46)
    ui.header(draw, head_r, weather.get("city", "—"),
              right_text=weather.get("updated", ""), fill=BLUE,
              title_size=21, right_size=13)

    rest_r = rest_r.inset(14, 12)
    hero, lower = rest_r.cut_top(int(rest_r.h * 0.42), gap=8)
    icon_box, reading = hero.cut_left(int(hero.w * 0.42), gap=4)
    ui.weather_icon(draw, icon_box, weather.get("icon"))

    temp_font = ui.font(66)
    draw.text((reading.x, reading.cy - 12), _temp(weather.get("temp")),
              fill=BLACK, font=temp_font, anchor="lm")
    draw.text((reading.x, reading.cy + 26),
              ui.fit(weather.get("description", ""), ui.font(17), reading.w),
              fill=BLACK, font=ui.font(17), anchor="lt")

    stats, forecast_box = lower.cut_top(58, gap=10)
    speed_unit = "mph" if weather.get("units") == "imperial" else "m/s"
    figures = [
        ("體感" if chinese else "Feels", _temp(weather.get("feels_like"))),
        ("濕度" if chinese else "Humidity", f"{weather.get('humidity', '—')}%"),
        ("風速" if chinese else "Wind", f"{weather.get('wind_speed', '—')} {speed_unit}"),
    ]
    for tile, (label, value) in zip(stats.cols(3, gap=6), figures):
        _draw_stat(draw, tile, label, value)

    forecast = weather.get("forecast") or []
    if forecast:
        label_box, days = forecast_box.cut_top(20, gap=2)
        draw.text((label_box.x, label_box.y), "未來三天" if chinese else "Next 3 days",
                  fill=BLUE, font=ui.font(14), anchor="lt")
        _draw_forecast_row(draw, days, forecast, lang)


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


def _event_days(events, now):
    """Which days of the current month carry an event, for the grid dots."""
    days = set()
    for event in events or []:
        start = event.get("start")
        if start and start.year == now.year and start.month == now.month:
            days.add(start.day)
    return days


def _draw_month_grid(draw, box, now, events=None, lang="en", compact=False):
    """A month calendar sized to whatever box it is given.

    Cell geometry is derived from the box rather than fixed, which is what
    stops the grid running past the bottom of a short panel — the old one used
    a 16px row height everywhere and overlapped the events beneath it in
    February and in any month that spans six weeks.
    """
    import calendar

    weeks = calendar.monthcalendar(now.year, now.month)
    marked = _event_days(events, now) if events else set()

    head_h = 18 if compact else 24
    head, body = box.cut_top(head_h, gap=3 if compact else 5)
    cell_w = box.w / 7
    cell_h = body.h / max(len(weeks), 1)
    radius = min(cell_w * 0.42, cell_h * 0.42)

    # Below 18 pixels a cell cannot hold a digit and a marker without the two
    # touching, so the markers are dropped rather than drawn on top.
    show_dots = cell_h >= 22
    label_font = ui.font(12 if compact else 15)
    day_font = ui.font(13 if compact else 17)

    for index in range(7):
        cx = box.x + index * cell_w + cell_w / 2
        draw.text((cx, head.y), ui.weekday_initial(index, lang),
                  fill=RED if index >= 5 else BLACK, font=label_font, anchor="mt")

    for row, week in enumerate(weeks):
        for col, day in enumerate(week):
            if day == 0:
                continue
            cx = box.x + col * cell_w + cell_w / 2
            cy = body.y + row * cell_h + cell_h / 2

            if day == now.day:
                draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                             fill=RED)
                draw.text((cx, cy), str(day), fill=WHITE, font=day_font, anchor="mm")
            else:
                draw.text((cx, cy), str(day),
                          fill=RED if col >= 5 else BLACK, font=day_font, anchor="mm")
                # A day with something on it gets a dot, so the month reads as
                # a schedule at a glance and not just as numbers.
                if day in marked and show_dots:
                    dot = max(1.5, cell_h * 0.065)
                    dy = cy + cell_h * 0.40
                    draw.ellipse([cx - dot, dy - dot, cx + dot, dy + dot], fill=BLUE)


def _draw_agenda(draw, box, events, lang="en", limit=3, compact=False):
    """The upcoming-events list: day on the left, time and title beside it."""
    title_font = ui.font(13 if compact else 16)
    meta_font = ui.font(11 if compact else 13)

    if not events:
        draw.text((box.x, box.y), "沒有行程" if ui.use_chinese(lang) else "No upcoming events",
                  fill=BLACK, font=meta_font, anchor="lt")
        return

    rows = [e for e in events if e.get("start")][:limit]
    if not rows:
        return
    row_h = min(box.h / len(rows), 46 if not compact else 30)

    for index, event in enumerate(rows):
        start = event["start"]
        top = box.y + index * row_h
        # A coloured tick beside each entry: cheaper to scan than a bullet, and
        # it survives the six-colour quantiser cleanly.
        draw.rectangle([box.x, top + 2, box.x + 3, top + row_h - 6], fill=BLUE)

        when = f"{ui.relative_day(start, lang)} {ui.time_label(start)}"
        draw.text((box.x + 10, top + 1), ui.fit(when, meta_font, box.w - 12),
                  fill=BLUE, font=meta_font, anchor="lt")
        draw.text((box.x + 10, top + (14 if compact else 17)),
                  ui.fit(event.get("summary", "?"), title_font, box.w - 12),
                  fill=BLACK, font=title_font, anchor="lt")


def _draw_calendar_panel(draw, x, y, w, h, events, lang="en"):
    """Calendar for a quarter panel: today, then what is coming up.

    Deliberately no month grid. A six-week month needs about 150 pixels to
    keep its digits and its event markers apart, and this panel has 240 for
    everything — cramming one in left the numbers touching their own dots and
    the agenda squeezed to a single clipped line. The grid lives in the
    calendar and split pages, which have the room for it.
    """
    box = ui.Box(x, y, w, h).inset(14, 12)
    now = datetime.now()
    chinese = ui.use_chinese(lang)

    today_box, rest = box.cut_top(84, gap=8)

    # Today, stated once and large enough to read across a room.
    day_font = ui.font(72)
    draw.text((today_box.x, today_box.y - 8), str(now.day),
              fill=BLACK, font=day_font, anchor="lt")
    offset = ui.text_width(str(now.day), day_font) + 12
    draw.text((today_box.x + offset, today_box.y + 8),
              ui.weekday_label(now.weekday(), lang, short=False),
              fill=RED if now.weekday() >= 5 else BLUE, font=ui.font(22), anchor="lt")
    draw.text((today_box.x + offset, today_box.y + 38),
              ui.month_label(now, lang), fill=BLACK, font=ui.font(15), anchor="lt")

    ui.rule(draw, ui.Box(box.x, rest.y - 5, box.w, 1), color=BLACK)

    label_box, list_box = rest.cut_top(18, gap=2)
    draw.text((label_box.x, label_box.y), "接下來" if chinese else "Coming up",
              fill=BLUE, font=ui.font(13), anchor="lt")
    _draw_agenda(draw, list_box, events, lang, limit=3, compact=True)


def _temp(value):
    """A temperature, or an em dash when the reading is missing."""
    return f"{value}°" if value is not None else "—"


# services/weather.py labels forecast days in English; the panel may not be.
_WEEKDAY_KEYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _localised_weekday(label, lang):
    if not label:
        return ""
    try:
        return ui.weekday_label(_WEEKDAY_KEYS.index(label), lang, short=True)
    except ValueError:
        return label


def _draw_forecast_row(draw, box, forecast, lang="en", compact=False):
    """The three-day strip: one column each, icon over day over high/low."""
    days = (forecast or [])[:3]
    if not days:
        return
    day_font = ui.font(12 if compact else 15)
    temp_font = ui.font(13 if compact else 17)

    # Two text lines sit under the icon, and rain and snow hang below the
    # icon's own centre — so the space is reserved from the bottom up rather
    # than assumed, which is what had "Sat" printed through the raindrops.
    text_h = (40 if compact else 42)
    for column, day in zip(box.cols(len(days), gap=4), days):
        icon_side = min(column.w * (0.54 if compact else 0.60), max(0, column.h - text_h))
        icon = ui.Box(column.cx - icon_side / 2, column.y, icon_side, icon_side)
        ui.weather_icon(draw, icon, day.get("icon"))

        label_y = column.bottom - text_h
        # The forecast carries English weekday keys; show them in the panel's
        # own language rather than leaving "Sat" on an otherwise Chinese page.
        label = _localised_weekday(day.get("weekday"), lang) or day.get("date", "")
        draw.text((column.cx, label_y), ui.fit(label, day_font, column.w),
                  fill=BLACK, font=day_font, anchor="mt")
        draw.text((column.cx, label_y + (14 if compact else 19)),
                  f"{_temp(day.get('temp_max'))} / {_temp(day.get('temp_min'))}",
                  fill=BLACK, font=temp_font, anchor="mt")


def _draw_weather_panel(draw, x, y, w, h, weather, lang="en"):
    """Compact weather for a quarter panel: icon, temperature, three days."""
    box = ui.Box(x, y, w, h).inset(12, 10)

    if not weather:
        ui.empty_state(draw, box,
                       "尚未設定天氣" if ui.use_chinese(lang) else "No weather data",
                       "Settings → Weather")
        return

    # Now: icon on the left, the number that matters next to it.
    now_box, forecast = box.cut_top(int(box.h * 0.52), gap=4)
    icon_box, text_box = now_box.cut_left(int(now_box.h * 0.95), gap=6)
    ui.weather_icon(draw, icon_box, weather.get("icon"))

    temp_font = ui.font(52)
    draw.text((text_box.x, text_box.y - 4), _temp(weather.get("temp")),
              fill=BLACK, font=temp_font, anchor="lt")
    draw.text((text_box.x, text_box.y + 52),
              ui.fit(weather.get("description", ""), ui.font(15), text_box.w),
              fill=BLACK, font=ui.font(15), anchor="lt")
    draw.text((text_box.x, text_box.y + 71),
              f"{_temp(weather.get('temp_max'))} / {_temp(weather.get('temp_min'))}"
              f"   {weather.get('humidity', '—')}%",
              fill=BLUE, font=ui.font(13), anchor="lt")

    ui.rule(draw, ui.Box(box.x, forecast.y - 4, box.w, 1), color=BLACK)
    _draw_forecast_row(draw, forecast, weather.get("forecast"), lang, compact=True)


def _draw_stat(draw, box, label, value, accent=BLUE):
    """One labelled figure in a bordered tile."""
    ui.card(draw, box, outline=BLACK, width=1)
    draw.text((box.cx, box.y + box.h * 0.30),
              ui.fit(label, ui.font(14), box.w - 12),
              fill=accent, font=ui.font(14), anchor="mm")
    draw.text((box.cx, box.y + box.h * 0.66),
              ui.fit(value, ui.font(24), box.w - 12),
              fill=BLACK, font=ui.font(24), anchor="mm")


def _draw_weather_fullscreen(draw, weather, lang="en"):
    """Full-screen weather: header, hero reading, four figures, three days."""
    page = ui.Box(0, 0, EPD_W, EPD_H)

    if not weather:
        ui.header(draw, ui.Box(0, 0, EPD_W, 56),
                  "天氣" if ui.use_chinese(lang) else "Weather", fill=BLUE)
        ui.empty_state(draw, ui.Box(0, 56, EPD_W, EPD_H - 56),
                       "尚未設定天氣" if ui.use_chinese(lang) else "No weather data",
                       "Settings → Weather")
        return

    chinese = ui.use_chinese(lang)
    head, body = page.cut_top(56)
    ui.header(draw, head, weather.get("city", "—"),
              right_text=f"{'更新' if chinese else 'Updated'} {weather.get('updated', '?')}",
              fill=BLUE)

    body = body.inset(18, 14)
    hero, lower = body.cut_top(int(body.h * 0.52), gap=12)

    # ── Hero: the icon and the temperature, side by side and large ──
    icon_box, reading = hero.cut_left(int(hero.w * 0.32), gap=8)
    ui.weather_icon(draw, icon_box, weather.get("icon"))

    # The temperature is the one thing read from across the room, so it sets
    # the baseline and everything else hangs off it.
    temp_font = ui.font(104)
    temp_text = _temp(weather.get("temp"))
    temp_w = ui.text_width(temp_text, temp_font)
    draw.text((reading.x, reading.cy), temp_text, fill=BLACK,
              font=temp_font, anchor="lm")

    side = reading.x + temp_w + 16
    side_w = max(0, reading.right - side)
    draw.text((side, reading.cy - 26),
              ui.fit(weather.get("description", ""), ui.font(24), side_w),
              fill=BLACK, font=ui.font(24), anchor="lm")
    draw.text((side, reading.cy + 4),
              f"{'體感' if chinese else 'Feels like'} {_temp(weather.get('feels_like'))}",
              fill=BLUE, font=ui.font(18), anchor="lm")
    draw.text((side, reading.cy + 30),
              f"{_temp(weather.get('temp_max'))} / {_temp(weather.get('temp_min'))}"
              f"  {'今日' if chinese else 'today'}",
              fill=BLACK, font=ui.font(18), anchor="lm")

    # ── Figures and forecast, side by side underneath ──
    stats_box, forecast_box = lower.cut_left(int(lower.w * 0.42), gap=16)

    speed_unit = "mph" if weather.get("units") == "imperial" else "m/s"
    top_row, bottom_row = stats_box.rows(2, gap=8)
    figures = [
        ("濕度" if chinese else "Humidity", f"{weather.get('humidity', '—')}%"),
        ("風速" if chinese else "Wind", f"{weather.get('wind_speed', '—')} {speed_unit}"),
        ("最高" if chinese else "High", _temp(weather.get("temp_max"))),
        ("最低" if chinese else "Low", _temp(weather.get("temp_min"))),
    ]
    tiles = top_row.cols(2, gap=8) + bottom_row.cols(2, gap=8)
    for tile, (label, value) in zip(tiles, figures):
        _draw_stat(draw, tile, label, value)

    forecast = weather.get("forecast") or []
    if forecast:
        label_box, days = forecast_box.cut_top(20)
        draw.text((label_box.x, label_box.y),
                  "未來三天" if chinese else "Next 3 days",
                  fill=BLUE, font=ui.font(15), anchor="lt")
        _draw_forecast_row(draw, days, forecast, lang)


def _draw_calendar_fullscreen(draw, events, lang="en"):
    """Full-screen calendar: month grid on the left, today and agenda right."""
    now = datetime.now()
    page = ui.Box(0, 0, EPD_W, EPD_H)
    chinese = ui.use_chinese(lang)

    head, body = page.cut_top(56)
    ui.header(draw, head, ui.month_label(now, lang),
              right_text=ui.time_label(now), fill=RED)

    body = body.inset(18, 14)
    grid_box, side = body.cut_left(int(body.w * 0.56), gap=18)

    _draw_month_grid(draw, grid_box, now, events, lang)

    # A vertical rule instead of a boxed card: one line, no wasted space.
    draw.line([(side.x - 9, side.y), (side.x - 9, side.bottom)], fill=BLACK, width=1)

    # ── Today, stated once and large ──
    today_box, agenda_box = side.cut_top(118, gap=10)
    day_font = ui.font(86)
    draw.text((today_box.x, today_box.y - 8), str(now.day),
              fill=BLACK, font=day_font, anchor="lt")
    offset = ui.text_width(str(now.day), day_font) + 14
    draw.text((today_box.x + offset, today_box.y + 16),
              ui.weekday_label(now.weekday(), lang, short=False),
              fill=RED if now.weekday() >= 5 else BLUE,
              font=ui.font(24), anchor="lt")
    draw.text((today_box.x + offset, today_box.y + 48),
              ui.date_label(now, lang), fill=BLACK, font=ui.font(17), anchor="lt")

    label_box, list_box = agenda_box.cut_top(22, gap=4)
    draw.text((label_box.x, label_box.y),
              "接下來" if chinese else "Coming up",
              fill=BLUE, font=ui.font(15), anchor="lt")
    _draw_agenda(draw, list_box, events, lang, limit=4)
