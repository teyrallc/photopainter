"""
Drawing toolkit for the e-paper panel.

The widget pages were written as a pile of absolute coordinates — every panel
computed its own magic numbers, text was cut by character count, and nothing
knew how wide anything else was. That is why they read as data dumped on a
screen rather than something designed: there was no layout, only offsets.

This module is the missing layer underneath. Three ideas, and everything else
follows from them:

**The panel has six colours and no greys.** A 7.3" Waveshare renders black,
white, yellow, red, blue and green, and anything in between is quantised to
the nearest one — so a "light grey" divider becomes a field of dithered noise.
Only the exact palette below is used, which is also why flat blocks of colour
are the cheapest way to build hierarchy here.

**Boxes, not coordinates.** `Box` slices a region into rows, columns and
insets. A panel says "the top 56 pixels are the header, split the rest in
two"; it never says 237.

**Text is measured, never counted.** `fit()` trims to the pixel width it is
given. Slicing at 20 characters cut Chinese titles mid-word and left English
ones with half the space empty.
"""

import logging
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("vignette.epd_ui")

# ── Palette ──────────────────────────────────────────────────────────────
# The exact six the panel can render. Every colour used anywhere on a page
# must come from this list, or the quantiser will dither it into noise.
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)

PALETTE = (BLACK, WHITE, RED, YELLOW, BLUE, GREEN)

# ── Fonts ────────────────────────────────────────────────────────────────
# CJK first: a frame set to Chinese has to render Chinese, and DejaVu has no
# glyphs for it — every character would come out as an empty box.
FONT_PATHS = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

# The subset of the above that can actually draw Chinese.
_CJK_FONTS = ("wqy-zenhei", "NotoSansCJK")

_font_cache = {}
_resolved_path = None


def font(size):
    """A font at `size`, cached. Falls back to PIL's bitmap font if nothing."""
    global _resolved_path
    if size in _font_cache:
        return _font_cache[size]

    for path in FONT_PATHS:
        if not os.path.exists(path):
            continue
        try:
            loaded = ImageFont.truetype(path, size, index=0)
        except Exception:  # noqa: BLE001 - a broken font file is just the next one
            continue
        if _resolved_path != path:
            # Said once, at the first draw: "why is my calendar entry a row of
            # boxes" is answered by this line in the journal.
            _resolved_path = path
            logger.info("Panel font: %s (CJK: %s)", path,
                        "yes" if any(n in path for n in _CJK_FONTS) else
                        "no — install fonts-wqy-zenhei")
        _font_cache[size] = loaded
        return loaded

    logger.warning("No usable font found in %s; the panel will fall back to a "
                   "bitmap face and its pages will be hard to read.", FONT_PATHS)
    fallback = ImageFont.load_default()
    _font_cache[size] = fallback
    return fallback


def has_cjk():
    """Can the panel actually draw Chinese on this device?

    Asked before choosing a language for labels: on a device with no CJK font
    installed, Chinese weekday names render as a row of empty boxes, which is
    worse than English. Resolve a font first so the answer is not "no" simply
    because nothing has been drawn yet.
    """
    font(16)
    return any(name in (_resolved_path or "") for name in _CJK_FONTS)


def resolved_font_path():
    """Which font file the panel is drawing with, for diagnostics."""
    font(16)
    return _resolved_path


# ── Text ─────────────────────────────────────────────────────────────────

_measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))


def text_width(text, fnt):
    """Rendered width of `text` in pixels."""
    if not text:
        return 0
    try:
        return int(_measure.textlength(text, font=fnt))
    except Exception:  # noqa: BLE001 - bitmap fallback fonts lack textlength
        bbox = fnt.getbbox(text)
        return bbox[2] - bbox[0] if bbox else 0


def text_height(fnt):
    """Line height for a font, measured off characters with ascender+descender."""
    bbox = fnt.getbbox("Ag日")
    return (bbox[3] - bbox[1]) if bbox else 12


def fit(text, fnt, max_w, tail="…"):
    """Trim `text` to `max_w` pixels, ending with an ellipsis when it had to.

    Measured, not counted: `summary[:20]` cut Chinese titles mid-word and
    wasted half the line on short English ones.
    """
    text = str(text or "")
    if not text or text_width(text, fnt) <= max_w:
        return text
    tail_w = text_width(tail, fnt)
    if tail_w > max_w:
        return ""
    trimmed = text
    while trimmed and text_width(trimmed, fnt) + tail_w > max_w:
        trimmed = trimmed[:-1]
    return (trimmed + tail) if trimmed else ""


def wrap(text, fnt, max_w, max_lines=2, tail="…"):
    """Break `text` into at most `max_lines` lines of `max_w` pixels.

    Wraps on spaces where there are any and per character where there are not,
    which is what Chinese needs — it has no spaces to break on.
    """
    text = str(text or "").strip()
    if not text:
        return []

    lines, current = [], ""
    tokens = text.split(" ") if " " in text else list(text)
    joiner = " " if " " in text else ""

    for token in tokens:
        candidate = f"{current}{joiner}{token}" if current else token
        if text_width(candidate, fnt) <= max_w:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = token
        if len(lines) == max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return []

    # Anything that did not fit is folded into an ellipsis on the last line.
    consumed = len(joiner.join(lines)) if joiner else len("".join(lines))
    if consumed < len(text):
        lines[-1] = fit(lines[-1] + (joiner or "") + "…", fnt, max_w, tail=tail)
    return lines[:max_lines]


# ── Layout ───────────────────────────────────────────────────────────────

class Box:
    """A rectangle that can be sliced. The whole layout system.

    Panels describe themselves in terms of regions — "header across the top,
    then two columns" — instead of arithmetic on absolute pixel positions,
    which is what made the old panels overlap whenever a font size changed.
    """

    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)

    # Edges and centres, so callers never recompute them.
    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h

    @property
    def cx(self):
        return self.x + self.w // 2

    @property
    def cy(self):
        return self.y + self.h // 2

    def inset(self, dx, dy=None):
        """Shrink on every side. Never returns a negative-sized box."""
        dy = dx if dy is None else dy
        return Box(self.x + dx, self.y + dy,
                   max(0, self.w - 2 * dx), max(0, self.h - 2 * dy))

    def cut_top(self, n, gap=0):
        """(top n pixels, what is left below it)."""
        n = min(n, self.h)
        return (Box(self.x, self.y, self.w, n),
                Box(self.x, self.y + n + gap, self.w, max(0, self.h - n - gap)))

    def cut_bottom(self, n, gap=0):
        n = min(n, self.h)
        return (Box(self.x, self.bottom - n, self.w, n),
                Box(self.x, self.y, self.w, max(0, self.h - n - gap)))

    def cut_left(self, n, gap=0):
        n = min(n, self.w)
        return (Box(self.x, self.y, n, self.h),
                Box(self.x + n + gap, self.y, max(0, self.w - n - gap), self.h))

    def cut_right(self, n, gap=0):
        n = min(n, self.w)
        return (Box(self.right - n, self.y, n, self.h),
                Box(self.x, self.y, max(0, self.w - n - gap), self.h))

    def rows(self, n, gap=0):
        """Split into `n` equal rows."""
        each = (self.h - gap * (n - 1)) / n if n else self.h
        return [Box(self.x, self.y + i * (each + gap), self.w, each) for i in range(n)]

    def cols(self, n, gap=0):
        """Split into `n` equal columns."""
        each = (self.w - gap * (n - 1)) / n if n else self.w
        return [Box(self.x + i * (each + gap), self.y, each, self.h) for i in range(n)]


# ── Chrome ───────────────────────────────────────────────────────────────

def header(draw, box, title, right_text=None, accent=BLUE,
           title_size=26, right_size=15, pad=18):
    """A title with a thin accent rule under it.

    Was a solid bar of red or blue across the full width. On a panel with six
    flat colours and no tints, that much saturation is the loudest thing in the
    room and it pushed everything else down — the page read as a warning, not
    as a calendar. A rule carries the same structure at a fraction of the ink.
    """
    title_font = font(title_size)
    baseline = box.bottom - 10

    reserved = 0
    if right_text:
        right_font = font(right_size)
        reserved = text_width(right_text, right_font) + 20
        draw.text((box.right - pad, baseline), right_text,
                  fill=BLACK, font=right_font, anchor="rs")

    draw.text((box.x + pad, baseline),
              fit(title, title_font, box.w - 2 * pad - reserved),
              fill=BLACK, font=title_font, anchor="ls")
    draw.line([(box.x + pad, box.bottom - 3), (box.right - pad, box.bottom - 3)],
              fill=accent, width=3)


def rule(draw, box, color=BLACK, width=1, vertical=False):
    """A divider along the middle of `box`."""
    if vertical:
        draw.line([(box.cx, box.y), (box.cx, box.bottom)], fill=color, width=width)
    else:
        draw.line([(box.x, box.cy), (box.right, box.cy)], fill=color, width=width)


def card(draw, box, outline=BLACK, width=1, fill=None, radius=6):
    """A bordered region. Grouping without grey, since grey is not available."""
    if fill is not None:
        draw.rounded_rectangle([box.x, box.y, box.right, box.bottom],
                               radius=radius, fill=fill)
    draw.rounded_rectangle([box.x, box.y, box.right, box.bottom],
                           radius=radius, outline=outline, width=width)


def empty_state(draw, box, message, hint=None):
    """What a panel shows when its data source is not configured yet."""
    body = font(20)
    draw.text((box.cx, box.cy - (12 if hint else 0)), fit(message, body, box.w - 24),
              fill=BLACK, font=body, anchor="mm")
    if hint:
        small = font(14)
        draw.text((box.cx, box.cy + 16), fit(hint, small, box.w - 24),
                  fill=BLUE, font=small, anchor="mm")


# ── Weather icons ────────────────────────────────────────────────────────
#
# Drawn as vectors rather than glyphs. The old code mapped conditions to emoji
# (☀ 🌧 ⛈), and neither DejaVu nor WenQuanYi has those code points — every one
# of them would have come out as an empty box. These also scale to any size
# and land on exact palette colours.

def _line(size):
    """Stroke weight.

    Thin and near-constant, the way a line-icon set is drawn: the weight is a
    property of the set, not of how big any one icon happens to be. Scaling it
    proportionally is what made the hero icon look like a marker drawing. Never
    below two pixels — a one-pixel stroke on an unantialiased panel breaks up.
    """
    return int(min(4, max(2, round(size * 0.026))))


def _sun(draw, cx, cy, size, rays=True):
    """A small open circle with eight detached rays."""
    from math import cos, sin, pi
    w = _line(size)
    r = size * 0.185
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=BLACK, width=w)
    if not rays:
        return
    inner, outer = r + size * 0.075, r + size * 0.165
    for i in range(8):
        angle = i * pi / 4
        draw.line([(cx + cos(angle) * inner, cy + sin(angle) * inner),
                   (cx + cos(angle) * outer, cy + sin(angle) * outer)],
                  fill=BLACK, width=w)


def _moon(draw, cx, cy, size):
    """A crescent: one disc with a second bitten out of it."""
    w = _line(size)
    r = size * 0.20
    # Outline the disc, then bite a second disc out of it in the page colour,
    # which removes both the fill and the arc that the bite crosses.
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=BLACK, width=w)
    draw.ellipse([cx - r * 1.62, cy - r * 1.22, cx + r * 0.34, cy + r * 1.22],
                 fill=WHITE)
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=296, end=64,
             fill=BLACK, width=w)


def _cloud(draw, cx, cy, size, fill=WHITE):
    """A cloud as one continuous outline.

    Three bumps over a flat base, drawn as a silhouette twice — grown by the
    stroke in black, then at true size in the fill — so the joins between the
    parts never show and the stroke is the same weight the whole way round.
    """
    w = _line(size)
    width = size * 0.94
    base = cy + width * 0.115

    def silhouette(grow, colour):
        for dx, dy, r in ((-0.255, -0.030, 0.180),
                          (-0.010, -0.185, 0.255),
                          (+0.245, -0.070, 0.205)):
            px, py, rr = cx + width * dx, base + width * dy, width * r + grow
            draw.ellipse([px - rr, py - rr, px + rr, py + rr], fill=colour)
        draw.rounded_rectangle(
            [cx - width * 0.435 - grow, base - width * 0.06 - grow,
             cx + width * 0.450 + grow, base + width * 0.105 + grow],
            radius=width * 0.075 + grow, fill=colour)

    silhouette(w, BLACK)
    silhouette(0, fill)


def _rain(draw, cx, cy, size, count=3, length=0.13):
    """Short parallel strokes, slanted the way rain is drawn."""
    w = _line(size)
    for i in range(count):
        x = cx + (i - (count - 1) / 2) * size * 0.135
        draw.line([(x + size * 0.035, cy), (x - size * 0.035, cy + size * length)],
                  fill=BLACK, width=w)


def _drizzle(draw, cx, cy, size, count=4):
    """Dots rather than strokes: lighter than rain, and reads as it."""
    r = max(1.5, size * 0.021)
    for i in range(count):
        x = cx + (i - (count - 1) / 2) * size * 0.105
        y = cy + (size * 0.045 if i % 2 else 0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=BLACK)


def _snow(draw, cx, cy, size, count=3):
    """Six-spoke flakes, or dots when there is not room to draw one.

    Below about 60 pixels a flake is three crossed strokes inside four pixels,
    which quantises to a black smudge. Dots stay legible all the way down.
    """
    from math import cos, sin, pi
    if size < 60:
        _drizzle(draw, cx, cy + size * 0.02, size, count=count)
        return
    w = max(2, _line(size) - 1)
    r = size * 0.055
    for i in range(count):
        x = cx + (i - (count - 1) / 2) * size * 0.155
        y = cy + size * 0.045
        for k in range(3):
            angle = k * pi / 3
            draw.line([(x - cos(angle) * r, y - sin(angle) * r),
                       (x + cos(angle) * r, y + sin(angle) * r)],
                      fill=BLACK, width=w)


def _bolt(draw, cx, cy, size):
    """A lightning bolt, filled — at this scale an outline closes up."""
    a = size * 0.115
    draw.polygon([(cx + a * 0.55, cy - a * 0.2), (cx - a * 0.95, cy + a * 1.35),
                  (cx - a * 0.10, cy + a * 1.35), (cx - a * 0.55, cy + a * 2.7),
                  (cx + a * 0.95, cy + a * 1.0), (cx + a * 0.10, cy + a * 1.0)],
                 fill=BLACK)


def _fog(draw, cx, cy, size, lines=3):
    """Stacked horizontal strokes of alternating length."""
    w = _line(size)
    for i in range(lines):
        y = cy + (i - (lines - 1) / 2) * size * 0.13
        half = size * (0.30 if i % 2 == 0 else 0.23)
        draw.line([(cx - half, y), (cx + half, y)], fill=BLACK, width=w)


def weather_icon(draw, box, code, size=None):
    """Draw the condition for an OpenWeatherMap icon code, centred in `box`.

    Unknown codes fall back to a plain cloud rather than drawing nothing, so a
    code added upstream later still leaves the layout intact.
    """
    size = size or min(box.w, box.h)
    cx, cy = box.cx, box.cy
    kind = (code or "")[:2]
    night = str(code or "").endswith("n")

    if kind == "01":                                     # clear
        (_moon if night else _sun)(draw, cx, cy, size)
    elif kind == "02":                                   # a few clouds
        # The luminary sits behind the cloud's right shoulder; the cloud is
        # drawn after it, and its white fill does the occluding.
        if night:
            _moon(draw, cx + size * 0.24, cy - size * 0.26, size * 0.80)
        else:
            _sun(draw, cx + size * 0.22, cy - size * 0.22, size * 0.62)
        _cloud(draw, cx - size * 0.06, cy + size * 0.11, size * 0.80)
    elif kind == "03":                                   # scattered
        _cloud(draw, cx, cy, size * 0.86)
    elif kind == "04":                                   # broken / overcast
        if size >= 42:
            _cloud(draw, cx + size * 0.15, cy - size * 0.15, size * 0.56)
            _cloud(draw, cx - size * 0.06, cy + size * 0.08, size * 0.84)
        else:
            _cloud(draw, cx, cy, size * 0.88)
    elif kind == "09":                                   # shower
        _cloud(draw, cx, cy - size * 0.13, size * 0.82)
        _rain(draw, cx, cy + size * 0.20, size, count=4)
    elif kind == "10":                                   # rain
        _cloud(draw, cx, cy - size * 0.13, size * 0.82)
        _rain(draw, cx, cy + size * 0.20, size, count=3, length=0.17)
    elif kind == "11":                                   # thunderstorm
        _cloud(draw, cx, cy - size * 0.15, size * 0.82)
        _bolt(draw, cx, cy + size * 0.13, size)
    elif kind == "13":                                   # snow
        _cloud(draw, cx, cy - size * 0.13, size * 0.82)
        _snow(draw, cx, cy + size * 0.17, size)
    elif kind == "50":                                   # mist
        _fog(draw, cx, cy, size)
    else:
        _cloud(draw, cx, cy, size * 0.86)


# ── Labels ───────────────────────────────────────────────────────────────
#
# The panel's own chrome is English, always. Content is a different matter: an
# event titled in Chinese, or a city name, is drawn as it comes, which is why
# the font stack above still leads with a CJK face. `has_cjk()` reports whether
# that face is actually installed, so "my calendar entries are empty boxes" has
# an answer in the log rather than being a mystery.

_WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAY_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday")
_WEEKDAY_INITIAL = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_MONTH = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def weekday_label(index, short=True):
    return (_WEEKDAY_SHORT if short else _WEEKDAY_LONG)[index]


def weekday_initial(index):
    """The two-letter form used in a calendar grid header."""
    return _WEEKDAY_INITIAL[index]


def month_label(when):
    return f"{_MONTH[when.month - 1]} {when.year}"


def date_label(when):
    # Built by hand rather than with %-d, which is not portable.
    return f"{when.strftime('%b')} {when.day}"


def time_label(when):
    return when.strftime("%H:%M")


def relative_day(when, now=None):
    """"Today" / "Tomorrow" / a weekday, for an agenda line."""
    now = now or datetime.now()
    delta = (when.date() - now.date()).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if 0 < delta < 7:
        return _WEEKDAY_SHORT[when.weekday()]
    return f"{when.month}/{when.day}"
