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
    """The default page: a photo, with the day's information beside it.

    Rebuilt around one narrow column rather than two stacked quarter-panels.
    The old arrangement gave calendar and weather a 400x240 box each and let
    them fight their own layouts inside it — a month grid that could not fit,
    a forecast squeezed under a temperature — while the photo, which is the
    reason the thing is on a wall, got the smaller share of a 800x480 panel.

    Now the column states the day once, top to bottom, at one rhythm: date,
    weather, the next three days, what is on. The photo takes everything else.
    """
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    COLUMN_W = 320
    _draw_home_column(img, draw, ui.Box(0, 0, COLUMN_W, EPD_H),
                      weather_data, calendar_events)
    draw.line([(COLUMN_W, 0), (COLUMN_W, EPD_H)], fill=BLACK, width=2)

    # Flatten the chrome before the photograph goes in: type and hairlines
    # want exact palette colours, and the photograph wants the driver's dither.
    # This returns a new image, so anything drawn after it needs a new handle.
    img = ui.flatten_to_palette(img)
    draw = ImageDraw.Draw(img)

    photo_box = ui.Box(COLUMN_W + 2, 0, EPD_W - COLUMN_W - 2, EPD_H)
    if photo_path and os.path.exists(photo_path):
        photo = _prepare_photo(photo_path, photo_box.w - 4, photo_box.h - 4,
                               config.get("photo_rotation", 0),
                               config.get("photo_fit_mode", "fit"))
        img.paste(photo, (photo_box.x + (photo_box.w - photo.width) // 2,
                          photo_box.y + (photo_box.h - photo.height) // 2))
    else:
        # The wordmark is a script face, so its drawn height has little to do
        # with its nominal size — the caption is placed under the ink itself,
        # measured, rather than at a guessed offset that used to overlap it.
        logo_font = _get_logo_font(68)
        _, top, _, bottom = draw.textbbox((photo_box.cx, photo_box.cy - 14),
                                          "Vignette", font=logo_font, anchor="mm")
        _draw_logo(draw, photo_box.cx, photo_box.cy - 14, 68, BLACK)
        draw.text((photo_box.cx, bottom + 14), "Smart Display",
                  fill=BLACK, font=ui.font(14), anchor="mt")

    return img


def _draw_home_column(img, draw, box, weather, events):
    """Date, weather, and what is on. Three things, and nothing else.

    The previous version also carried a three-day forecast and a fourth rule,
    which is how a 300-pixel column ends up looking like a spreadsheet. The
    forecast has a whole page of its own; here it was one more row of numbers
    competing with the two facts anybody actually glances over for — what day
    it is, and whether to take a coat.
    """
    now = datetime.now()
    inner = box.inset(18, 20)

    # ── The date, given the room to be the headline ──
    date_box, rest = inner.cut_top(104, gap=14)
    _, text_axis = _draw_date_block(draw, date_box, now, 86, 22, 15, gap=13)

    # ── Weather: an icon, a number, and one line of detail ──
    if weather:
        top_rule_y = rest.y - 7
        ui.rule(draw, ui.Box(inner.x, top_rule_y, inner.w, 1))
        now_box, rest = rest.cut_top(118, gap=14)
        bottom_rule_y = rest.y - 7

        # The band between the two rules is the unit here, so the icon is
        # centred in *that* — not hung off the temperature's midline, which put
        # it visibly high in a band it shares with two lines of text below.
        band_cy = (top_rule_y + bottom_rule_y) / 2
        reading_x = text_axis
        icon_side = min(84, reading_x - now_box.x - 12)
        ui.weather_icon(img, ui.Box((now_box.x + reading_x - icon_side) / 2,
                                    band_cy - icon_side / 2,
                                    icon_side, icon_side), weather.get("icon"))

        # The reading beside it is a three-line block, centred on the same line
        # so the two halves of the band balance. Its left edge is the axis the
        # weekday above sits on, so the column has one secondary edge, not one
        # per row.
        temp_font = ui.display(58, "medium")
        desc_font = ui.face(_describe(weather), 16, "regular")
        range_font = ui.display(15, "medium")
        block_h = 58 * 0.72 + 20 + ui.text_height(desc_font)
        baseline = band_cy - block_h / 2 + 58 * 0.72

        draw.text((reading_x, baseline), _temp(weather.get("temp")),
                  fill=BLACK, font=temp_font, anchor="ls")
        draw.text((reading_x, baseline + 12),
                  ui.fit(_describe(weather), desc_font, now_box.right - reading_x),
                  fill=BLACK, font=desc_font, anchor="lt")
        draw.text((reading_x, baseline + 12 + ui.text_height(desc_font) + 4),
                  f"{_temp(weather.get('temp_max'))} / {_temp(weather.get('temp_min'))}",
                  fill=BLUE, font=range_font, anchor="lt")

    # ── Whatever is on, with all the space that is left ──
    if rest.h < 40:
        return
    ui.rule(draw, ui.Box(inner.x, rest.y - 7, inner.w, 1))
    label_box, list_box = rest.cut_top(20, gap=6)
    draw.text((label_box.x, label_box.y), "Coming up",
              fill=BLUE, font=ui.display(14, "semibold"), anchor="lt")
    _draw_agenda(draw, list_box, events)


def render_widget_page(mode, weather_data, calendar_events):
    """Render full-screen widget: weather, calendar, or split (both)."""
    img = Image.new("RGB", (EPD_W, EPD_H), WHITE)
    draw = ImageDraw.Draw(img)

    if mode == "split":
        _draw_split_page(img, draw, weather_data, calendar_events)
    elif mode == "weather":
        _draw_weather_fullscreen(img, draw, weather_data)
    else:
        _draw_calendar_fullscreen(draw, calendar_events)

    # No photograph on these pages, so nothing here wants dithering.
    return ui.flatten_to_palette(img)


def _draw_split_page(img, draw, weather, events):
    """Calendar and weather side by side, each given a full-height column.

    Not the quarter-panel layouts stretched: those are designed for 240 pixels
    of height, and simply handing them 480 left a band of white through the
    middle of both halves. Each column is composed for the space it has.
    """
    now = datetime.now()
    left = ui.Box(0, 0, 400, EPD_H)
    right = ui.Box(400, 0, 400, EPD_H)
    draw.line([(400, 0), (400, EPD_H)], fill=BLACK, width=2)

    # ── Left: the month, then what is on ──
    head, rest = left.cut_top(46)
    ui.header(draw, head, ui.month_label(now), accent=RED, title_size=21)

    rest = rest.inset(14, 12)
    today_box, below = rest.cut_top(72, gap=8)
    _draw_date_block(draw, today_box, now, 60, 20, 15, gap=12)

    grid_box, agenda_box = below.cut_top(int(below.h * 0.52), gap=10)
    _draw_month_grid(draw, grid_box, now, events)

    label_box, list_box = agenda_box.cut_top(20, gap=2)
    draw.text((label_box.x, label_box.y), "Coming up",
              fill=BLUE, font=ui.font(14), anchor="lt")
    _draw_agenda(draw, list_box, events)

    # ── Right: now, then the next three days ──
    if not weather:
        head_r, rest_r = right.cut_top(46)
        ui.header(draw, head_r, "Weather", accent=BLUE, title_size=21)
        ui.empty_state(draw, rest_r,
                       "No weather data",
                       "Settings → Weather")
        return

    head_r, rest_r = right.cut_top(46)
    # No clock in the corner. The panel is on a wall next to a calendar and a
    # temperature; what it needs to say is which city and how warm, and the
    # time the reading was fetched at is of interest to nobody standing in
    # front of it. The calendar half lost its clock for the same reason.
    ui.header(draw, head_r, weather.get("city", "—"), accent=BLUE,
              title_size=21)

    rest_r = rest_r.inset(14, 12)
    hero, lower = rest_r.cut_top(int(rest_r.h * 0.42), gap=8)
    icon_col, reading = hero.cut_left(int(hero.w * 0.42), gap=4)

    # Bounded, and centred on the same line as the reading beside it. Filling
    # the column made the cloud the loudest thing on the half — larger than the
    # temperature it belongs to — and rain and snow hang below their own centre,
    # so at that size the icon ran past the bottom of the band it sits in.
    icon_side = min(icon_col.w, hero.h) * 0.62
    ui.weather_icon(img, ui.Box(icon_col.cx - icon_side / 2,
                                hero.cy - icon_side / 2,
                                icon_side, icon_side), weather.get("icon"))

    # Two lines, centred on that same line, so the two halves of the band
    # balance instead of each starting wherever its own box did.
    temp_font = ui.display(60, "medium")
    desc_font = ui.face(_describe(weather), 17, "regular")
    block_h = 60 * 0.72 + 10 + ui.text_height(desc_font)
    baseline = hero.cy - block_h / 2 + 60 * 0.72
    draw.text((reading.x, baseline), _temp(weather.get("temp")),
              fill=BLACK, font=temp_font, anchor="ls")
    draw.text((reading.x, baseline + 10),
              ui.fit(_describe(weather), desc_font, reading.w),
              fill=BLACK, font=desc_font, anchor="lt")

    stats, forecast_box = lower.cut_top(58, gap=10)
    speed_unit = "mph" if weather.get("units") == "imperial" else "m/s"
    figures = [
        ("Feels", _temp(weather.get("feels_like"))),
        ("Humidity", f"{weather.get('humidity', '—')}%"),
        ("Wind", f"{weather.get('wind_speed', '—')} {speed_unit}"),
    ]
    for tile, (label, value) in zip(stats.cols(3, gap=6), figures):
        _draw_stat(draw, tile, label, value)

    forecast = weather.get("forecast") or []
    if forecast:
        label_box, days = forecast_box.cut_top(20, gap=2)
        draw.text((label_box.x, label_box.y), "Next 3 days",
                  fill=BLUE, font=ui.font(14), anchor="lt")
        _draw_forecast_row(img, draw, days, forecast)


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


# Three dots is what a cell can hold without them touching the day number or
# each other; a fourth subscribed calendar falling on the same day is not
# worth making the other three illegible for.
MAX_DAY_DOTS = 3


def _event_days(events, now):
    """Which days of the current month carry an event, and in what colours.

    Returns {day: [ink, ...]} — one ink per calendar with something on that
    day, in the order the calendars are subscribed, so the same feed keeps the
    same position across the month instead of shuffling day by day.
    """
    days = {}
    for event in events or []:
        start = event.get("start")
        if not (start and start.year == now.year and start.month == now.month):
            continue
        ink = ui.calendar_ink(event.get("color"))
        inks = days.setdefault(start.day, [])
        if ink not in inks:
            inks.append(ink)
    return days


def _draw_month_grid(draw, box, now, events=None, compact=False):
    """A month calendar sized to whatever box it is given.

    Cell geometry is derived from the box rather than fixed, which is what
    stops the grid running past the bottom of a short panel — the old one used
    a 16px row height everywhere and overlapped the events beneath it in
    February and in any month that spans six weeks.
    """
    import calendar

    weeks = calendar.monthcalendar(now.year, now.month)
    marked = _event_days(events, now) if events else {}

    head_h = 18 if compact else 24
    head, body = box.cut_top(head_h, gap=3 if compact else 5)
    cell_w = box.w / 7
    cell_h = body.h / max(len(weeks), 1)
    radius = min(cell_w * 0.42, cell_h * 0.42)

    # Below 18 pixels a cell cannot hold a digit and a marker without the two
    # touching, so the markers are dropped rather than drawn on top.
    show_dots = cell_h >= 22
    # Weekday initials and day numbers: Latin by construction either way.
    label_font = ui.display(12 if compact else 15, "medium")
    day_font = ui.display(13 if compact else 17, "medium")

    for index in range(7):
        cx = box.x + index * cell_w + cell_w / 2
        draw.text((cx, head.y), ui.weekday_initial(index),
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
                # a schedule at a glance and not just as numbers — one dot per
                # calendar, in that calendar's colour.
                inks = marked.get(day, [])[:MAX_DAY_DOTS] if show_dots else []
                if inks:
                    dot = max(1.5, cell_h * 0.065)
                    dy = cy + cell_h * 0.40
                    # Centred as a group under the numeral, so a day with three
                    # dots still hangs off the same axis as a day with one.
                    step = dot * 2 + max(1.5, dot * 0.9)
                    first = cx - step * (len(inks) - 1) / 2
                    for slot, ink in enumerate(inks):
                        dx = first + slot * step
                        # A yellow disc this small all but disappears on white
                        # paper, so it is given an edge to be seen by. The
                        # other three need none and are left alone.
                        draw.ellipse([dx - dot, dy - dot, dx + dot, dy + dot],
                                     fill=ink,
                                     outline=BLACK if ink == YELLOW else None)


# What "coming up" covers: today and the two days after it. Beyond that a
# frame on a wall is not a planner — it is answering "is there anything on?",
# and a meeting next Thursday is not the answer to that question.
AGENDA_DAYS = 3


def _agenda_rows(events, now=None):
    """The events worth showing, soonest first, within the window."""
    now = now or datetime.now()
    horizon = now.date() + timedelta(days=AGENDA_DAYS - 1)
    rows = [e for e in events or [] if e.get("start")
            and e["start"].date() <= horizon
            # An event that finished hours ago is not coming up. Anything
            # still to happen today is, which is why this compares to now
            # rather than to the start of the day.
            and e["start"] >= now.replace(hour=0, minute=0, second=0, microsecond=0)]
    return sorted(rows, key=lambda e: e["start"])


def _draw_agenda(draw, box, events, limit=None, compact=False):
    """The upcoming-events list: day on the left, time and title beside it.

    Shows as many of the next few days' events as the box can hold at a fixed
    row height, rather than a fixed count stretched to fill it. Three entries
    spread over 200 pixels read as a page with nothing on it; the same 200
    pixels hold four or five at the height the type actually needs.
    """
    title_size = 13 if compact else 16
    # Titles come from somebody's calendar, so they may be Chinese; the times
    # beside them never are.
    meta_font = ui.display(11 if compact else 13, "medium")

    candidates = _agenda_rows(events)
    if not candidates:
        draw.text((box.x, box.y), "Nothing in the next few days",
                  fill=BLACK, font=meta_font, anchor="lt")
        return

    # 40px holds an 11px time over a 16px title with air to spare — the 46 it
    # was left a third of a row's worth of space unused at the foot of every
    # column, which is one fewer event shown for nothing.
    row_h = 28 if compact else 40
    capacity = max(1, int(box.h // row_h))
    if limit:
        capacity = min(capacity, limit)
    rows = candidates[:capacity]

    for index, event in enumerate(rows):
        start = event["start"]
        top = box.y + index * row_h
        # A coloured tick beside each entry: cheaper to scan than a bullet, and
        # it survives the six-colour quantiser cleanly. Its colour is the
        # calendar's, which is the whole of how two feeds are told apart.
        #
        # An event may also carry a colour of its own (RFC 7986 COLOR). When
        # it does and it differs, the tick is split: the calendar's colour on
        # top, the event's underneath, so one mark answers both "whose
        # calendar" and "which kind of thing". Note that Google's iCal export
        # publishes no colour at all — a Google feed will always be solid.
        ink = ui.calendar_ink(event.get("color"))
        own = ui.nearest_calendar_ink(event.get("event_color"))
        y0, y1 = top + 2, top + row_h - 6
        if own and own != ink:
            middle = (y0 + y1) / 2
            draw.rectangle([box.x, y0, box.x + 3, middle], fill=ink)
            draw.rectangle([box.x, middle, box.x + 3, y1], fill=own)
        else:
            draw.rectangle([box.x, y0, box.x + 3, y1], fill=ink)

        when = ui.when_label(start, event.get("all_day"))
        draw.text((box.x + 10, top + 1), ui.fit(when, meta_font, box.w - 12),
                  fill=ui.calendar_text_ink(event.get("color")),
                  font=meta_font, anchor="lt")
        summary = event.get("summary", "?")
        title_font = ui.face(summary, title_size, "regular")
        draw.text((box.x + 10, top + (14 if compact else 17)),
                  ui.fit(summary, title_font, box.w - 12),
                  fill=BLACK, font=title_font, anchor="lt")


def _draw_date_block(draw, box, now, day_size, label_size, sub_size, gap=14):
    """The day number, its weekday and its date — on two shared axes.

    Everything here is placed against something else rather than at a chosen
    offset: the numeral and the date line share one baseline, and the weekday
    and the date share one left edge. Before this the three pieces each had
    their own y, which is what made the block look scattered next to a numeral
    that large.

    Returns (baseline, text_x) so a caller can hang a rule off the baseline and
    line a later row up on the same secondary axis the weekday sits on.
    """
    # The numeral is the largest thing on any of these pages, so it is drawn in
    # the display face; the weekday and date beside it follow so the block
    # reads as one piece of typography rather than two.
    day_font = ui.display(day_size, "medium")
    label_font = ui.face(ui.weekday_label(now.weekday(), short=False),
                         label_size, "medium")
    sub_font = ui.face(ui.date_label(now), sub_size, "regular")
    # Baseline anchors ("ls") place text by its baseline rather than its box,
    # which is the only way two different sizes can be made to sit on one line.
    baseline = box.y + day_size * 0.76
    draw.text((box.x, baseline), str(now.day),
              fill=BLACK, font=day_font, anchor="ls")

    text_x = box.x + ui.text_width(str(now.day), day_font) + gap
    draw.text((text_x, baseline), ui.date_label(now),
              fill=BLACK, font=sub_font, anchor="ls")
    draw.text((text_x, baseline - ui.text_height(sub_font) - label_size * 0.42),
              ui.weekday_label(now.weekday(), short=False),
              fill=RED if now.weekday() >= 5 else BLUE,
              font=label_font, anchor="ls")
    return baseline, text_x


def _ink_center(draw, text, fnt, x, anchor):
    """Where the drawn ink of `text` would actually be centred.

    Anchors place text by its advance width, which includes the side bearings —
    and those grow with the type size. So a 104px reading and a 17px one, both
    "centred" on the same column, still land several pixels apart. Aligning two
    sizes to each other means aligning the ink.
    """
    left, _, right, _ = draw.textbbox((x, 0), text, font=fnt, anchor=anchor)
    return (left + right) / 2


def _describe(weather_or_day):
    """OpenWeatherMap sends "scattered clouds"; the panel wants a label.

    Title-cased at the point of drawing rather than in the service, so the API
    keeps returning exactly what upstream said.
    """
    return str((weather_or_day or {}).get("description") or "").title()


def _temp(value):
    """A temperature, or an em dash when the reading is missing."""
    return f"{value}°" if value is not None else "—"



def _draw_forecast_row(img, draw, box, forecast, compact=False):
    """The three-day strip: one column each, icon over day over high/low."""
    days = (forecast or [])[:3]
    if not days:
        return
    day_font = ui.display(12 if compact else 15, "regular")
    temp_font = ui.display(13 if compact else 17, "medium")

    # Two text lines sit under the icon, and rain and snow hang below the
    # icon's own centre — so the space is reserved from the bottom up rather
    # than assumed, which is what had "Sat" printed through the raindrops.
    text_h = (40 if compact else 42)
    for column, day in zip(box.cols(len(days), gap=4), days):
        icon_side = min(column.w * (0.54 if compact else 0.60), max(0, column.h - text_h))
        icon = ui.Box(column.cx - icon_side / 2, column.y, icon_side, icon_side)
        ui.weather_icon(img, icon, day.get("icon"))

        label_y = column.bottom - text_h
        label = day.get("weekday") or day.get("date", "")
        draw.text((column.cx, label_y), ui.fit(label, day_font, column.w),
                  fill=BLACK, font=day_font, anchor="mt")
        draw.text((column.cx, label_y + (14 if compact else 19)),
                  f"{_temp(day.get('temp_max'))} / {_temp(day.get('temp_min'))}",
                  fill=BLACK, font=temp_font, anchor="mt")


def _draw_stat(draw, box, label, value, accent=BLUE):
    """One labelled figure in a bordered tile."""
    ui.card(draw, box, outline=BLACK, width=1)
    label_font = ui.display(14, "medium")
    value_font = ui.display(24, "medium")
    draw.text((box.cx, box.y + box.h * 0.30),
              ui.fit(label, label_font, box.w - 12),
              fill=accent, font=label_font, anchor="mm")
    draw.text((box.cx, box.y + box.h * 0.66),
              ui.fit(value, value_font, box.w - 12),
              fill=BLACK, font=value_font, anchor="mm")


def _draw_weather_fullscreen(img, draw, weather):
    """Full-screen weather: header, hero reading, four figures, three days."""
    page = ui.Box(0, 0, EPD_W, EPD_H)

    if not weather:
        ui.header(draw, ui.Box(0, 0, EPD_W, 56), "Weather", accent=BLUE)
        ui.empty_state(draw, ui.Box(0, 56, EPD_W, EPD_H - 56),
                       "No weather data",
                       "Settings → Weather")
        return

    head, body = page.cut_top(56)
    # The last of the three corner clocks, gone the same way as the calendar's
    # and the split view's. A panel on a wall is read at a glance for the city
    # and the temperature; when the reading was fetched is a fact about the
    # software, not about the weather.
    ui.header(draw, head, weather.get("city", "—"), accent=BLUE)

    body = body.inset(18, 14)
    hero, lower = body.cut_top(int(body.h * 0.52), gap=12)

    # The two blocks underneath set the page's vertical axes, so they are
    # measured first and the hero is hung off them. Splitting the hero on its
    # own fraction is what left the icon and the tiles beneath it on different
    # centres, and the temperature aligned to nothing at all.
    stats_box, forecast_box = lower.cut_left(int(lower.w * 0.42), gap=16)
    forecast = (weather.get("forecast") or [])[:3]
    label_box, forecast_days = forecast_box.cut_top(20)

    # ── Hero, on those axes: icon centred over the tiles, text over the days ──
    icon_side = min(hero.h, stats_box.w * 0.62)
    ui.weather_icon(img, ui.Box(stats_box.cx - icon_side / 2,
                                hero.cy - icon_side / 2, icon_side, icon_side),
                    weather.get("icon"))

    temp_font = ui.display(104, "medium")
    temp_text = _temp(weather.get("temp"))
    temp_w = ui.text_width(temp_text, temp_font)

    # Today's reading sits directly above the first day's high/low, on one line
    # straight down the page — matched on the ink, not on the anchor, because
    # at 104px against 17px the side bearings alone are worth several pixels.
    if forecast:
        column = forecast_days.cols(len(forecast), gap=4)[0]
        first_range = (f"{_temp(forecast[0].get('temp_max'))} / "
                       f"{_temp(forecast[0].get('temp_min'))}")
        target = _ink_center(draw, first_range, ui.display(17, "medium"),
                             column.cx, "mt")
        temp_cx = column.cx + (
            target - _ink_center(draw, temp_text, temp_font, column.cx, "mm"))
    else:
        temp_cx = forecast_box.x + temp_w / 2
    draw.text((temp_cx, hero.cy), temp_text,
              fill=BLACK, font=temp_font, anchor="mm")

    side = temp_cx + temp_w / 2 + 18
    side_w = max(0, hero.right - side)
    draw.text((side, hero.cy - 26),
              ui.fit(_describe(weather), ui.font(24), side_w),
              fill=BLACK, font=ui.font(24), anchor="lm")
    draw.text((side, hero.cy + 4),
              f"Feels like {_temp(weather.get('feels_like'))}",
              fill=BLUE, font=ui.font(18), anchor="lm")
    draw.text((side, hero.cy + 30),
              f"{_temp(weather.get('temp_max'))} / {_temp(weather.get('temp_min'))}"
              f"  today",
              fill=BLACK, font=ui.font(18), anchor="lm")

    # ── The figures themselves ──
    speed_unit = "mph" if weather.get("units") == "imperial" else "m/s"
    top_row, bottom_row = stats_box.rows(2, gap=8)
    figures = [
        ("Humidity", f"{weather.get('humidity', '—')}%"),
        ("Wind", f"{weather.get('wind_speed', '—')} {speed_unit}"),
        ("High", _temp(weather.get("temp_max"))),
        ("Low", _temp(weather.get("temp_min"))),
    ]
    tiles = top_row.cols(2, gap=8) + bottom_row.cols(2, gap=8)
    for tile, (label, value) in zip(tiles, figures):
        _draw_stat(draw, tile, label, value)

    if forecast:
        draw.text((label_box.x, label_box.y),
                  "Next 3 days",
                  fill=BLUE, font=ui.font(15), anchor="lt")
        _draw_forecast_row(img, draw, forecast_days, forecast)


def _draw_calendar_fullscreen(draw, events):
    """Full-screen calendar: month grid on the left, today and agenda right."""
    now = datetime.now()
    page = ui.Box(0, 0, EPD_W, EPD_H)

    head, body = page.cut_top(56)
    ui.header(draw, head, ui.month_label(now), accent=RED)

    # A solid block of red is heavy; the grid needs to breathe under it or the
    # whole page reads as one crowded slab.
    body = body.inset(18, 16)
    grid_box, side = body.cut_left(int(body.w * 0.56), gap=18)

    _draw_month_grid(draw, grid_box, now, events)

    # A vertical rule instead of a boxed card: one line, no wasted space.
    draw.line([(side.x - 9, side.y), (side.x - 9, side.bottom)], fill=BLACK, width=1)

    # ── Today, stated once and large ──
    today_box, agenda_box = side.cut_top(118, gap=10)
    _draw_date_block(draw, today_box, now, 86, 24, 17)

    label_box, list_box = agenda_box.cut_top(22, gap=4)
    draw.text((label_box.x, label_box.y),
              "Coming up",
              fill=BLUE, font=ui.font(15), anchor="lt")
    _draw_agenda(draw, list_box, events)
