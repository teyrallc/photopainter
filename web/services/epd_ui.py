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

def header(draw, box, title, right_text=None, fill=BLACK, fg=WHITE,
           title_size=26, right_size=15):
    """A solid title bar. Flat colour, which is what e-paper renders best."""
    draw.rectangle([box.x, box.y, box.right, box.bottom], fill=fill)
    title_font = font(title_size)
    reserved = 0
    if right_text:
        right_font = font(right_size)
        reserved = text_width(right_text, right_font) + 24
        draw.text((box.right - 14, box.cy), right_text,
                  fill=fg, font=right_font, anchor="rm")
    draw.text((box.x + 14, box.cy),
              fit(title, title_font, box.w - 28 - reserved),
              fill=fg, font=title_font, anchor="lm")


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

def _sun(draw, cx, cy, r, rays=True):
    if rays:
        for i in range(8):
            from math import cos, sin, pi
            angle = i * pi / 4
            x1, y1 = cx + cos(angle) * (r + r * 0.35), cy + sin(angle) * (r + r * 0.35)
            x2, y2 = cx + cos(angle) * (r + r * 0.85), cy + sin(angle) * (r + r * 0.85)
            draw.line([(x1, y1), (x2, y2)], fill=YELLOW, width=max(2, int(r * 0.18)))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=YELLOW, outline=BLACK,
                 width=max(1, int(r * 0.12)))


def _moon(draw, cx, cy, r):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=YELLOW, outline=BLACK,
                 width=max(1, int(r * 0.12)))
    # Bite out of the disc, in the page colour, to make the crescent.
    draw.ellipse([cx - r * 1.5, cy - r * 1.25, cx + r * 0.35, cy + r * 1.25],
                 fill=WHITE, outline=None)
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=300, end=60,
             fill=BLACK, width=max(1, int(r * 0.12)))


def _cloud(draw, cx, cy, w, fill=WHITE, outline=BLACK):
    """A cloud `w` wide, centred on (cx, cy).

    Drawn as a silhouette twice — once grown by the stroke width in the
    outline colour, once at true size in the fill colour. Stroking each puff
    individually instead leaves the seams between them showing straight
    through the middle of the cloud, which is what the first version did.
    """
    line = max(2, w * 0.05)
    base = cy + w * 0.14

    def silhouette(grow, colour):
        puffs = (
            (cx - w * 0.05, base - w * 0.13, w * 0.27),   # tall centre puff
            (cx - w * 0.27, base - w * 0.01, w * 0.20),   # left shoulder
            (cx + w * 0.24, base - w * 0.04, w * 0.22),   # right shoulder
        )
        for px, py, r in puffs:
            r += grow
            draw.ellipse([px - r, py - r, px + r, py + r], fill=colour)
        # The flat base that ties the puffs together.
        draw.rounded_rectangle(
            [cx - w * 0.46 - grow, base - w * 0.06 - grow,
             cx + w * 0.46 + grow, base + w * 0.16 + grow],
            radius=w * 0.08 + grow, fill=colour)

    silhouette(line, outline)
    silhouette(0, fill)


def _drops(draw, cx, cy, w, color=BLUE, count=3):
    line = max(2, int(w * 0.055))
    for i in range(count):
        x = cx + (i - (count - 1) / 2) * w * 0.22
        draw.line([(x, cy), (x - w * 0.05, cy + w * 0.20)], fill=color, width=line)


def _flakes(draw, cx, cy, w, color=BLUE, count=3):
    line = max(1, int(w * 0.04))
    for i in range(count):
        x = cx + (i - (count - 1) / 2) * w * 0.22
        s = w * 0.08
        draw.line([(x - s, cy + s), (x + s, cy + s * 3)], fill=color, width=line)
        draw.line([(x - s, cy + s * 3), (x + s, cy + s)], fill=color, width=line)
        draw.line([(x, cy + s * 0.5), (x, cy + s * 3.5)], fill=color, width=line)


def _bolt(draw, cx, cy, w, color=YELLOW):
    s = w * 0.22
    draw.polygon([(cx + s * 0.25, cy), (cx - s * 0.55, cy + s * 1.15),
                  (cx - s * 0.05, cy + s * 1.15), (cx - s * 0.35, cy + s * 2.2),
                  (cx + s * 0.65, cy + s * 0.9), (cx + s * 0.1, cy + s * 0.9)],
                 fill=color, outline=BLACK)


def weather_icon(draw, box, code, size=None):
    """Draw the condition for an OpenWeatherMap icon code, centred in `box`.

    Unknown codes fall back to a cloud rather than drawing nothing, so a code
    Apple or OWM adds later still leaves the layout intact.
    """
    size = size or min(box.w, box.h)
    cx, cy = box.cx, box.cy
    kind = (code or "")[:2]
    night = str(code or "").endswith("n")
    r = size * 0.26

    if kind == "01":
        if night:
            _moon(draw, cx, cy, r * 1.1)
        else:
            _sun(draw, cx, cy, r)
    elif kind == "02":
        # Sun (or moon) peeking out behind a cloud.
        if night:
            _moon(draw, cx + size * 0.16, cy - size * 0.18, r * 0.72)
        else:
            _sun(draw, cx + size * 0.16, cy - size * 0.18, r * 0.62)
        _cloud(draw, cx - size * 0.06, cy + size * 0.06, size * 0.78)
    elif kind in ("03", "04"):
        if kind == "04":
            _cloud(draw, cx + size * 0.10, cy - size * 0.10, size * 0.60)
        _cloud(draw, cx - size * 0.04, cy + size * 0.04, size * 0.82)
    elif kind in ("09", "10"):
        _cloud(draw, cx, cy - size * 0.10, size * 0.80)
        _drops(draw, cx, cy + size * 0.24, size, count=3 if kind == "09" else 2)
    elif kind == "11":
        _cloud(draw, cx, cy - size * 0.12, size * 0.80)
        _bolt(draw, cx, cy + size * 0.14, size)
    elif kind == "13":
        _cloud(draw, cx, cy - size * 0.10, size * 0.78)
        _flakes(draw, cx, cy + size * 0.16, size)
    elif kind == "50":
        line = max(2, int(size * 0.055))
        for i in range(4):
            y = cy - size * 0.18 + i * size * 0.14
            indent = size * (0.08 if i % 2 else 0.0)
            draw.line([(cx - size * 0.32 + indent, y), (cx + size * 0.32 - indent, y)],
                      fill=BLUE, width=line)
    else:
        _cloud(draw, cx, cy, size * 0.82)


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
