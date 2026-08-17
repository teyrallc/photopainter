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

# ── The display face ─────────────────────────────────────────────────────
# One face cannot do both jobs. The stack above is chosen for coverage — it has
# to draw Chinese — and WenQuanYi's Latin numerals are its weakest part: uneven
# widths and a thin, papery stroke that the panel's hard threshold breaks up.
# At 86 and 104 pixels those numerals *are* the page, so they get a face chosen
# for exactly that: Inter, at a weight heavy enough to survive thresholding and
# to read from across a room.
#
# It is Latin-only, so `face()` below picks it only for strings that have no
# CJK in them, and everything falls back to the body stack when Inter is not
# installed. Nothing here is required — the panel just looks plainer without it.
_DISPLAY_PATHS = {
    "regular": [
        "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
        "/usr/share/fonts/truetype/inter/Inter-Regular.ttf",
    ],
    "medium": [
        "/usr/share/fonts/opentype/inter/Inter-Medium.otf",
        "/usr/share/fonts/truetype/inter/Inter-Medium.ttf",
    ],
    "semibold": [
        "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf",
        "/usr/share/fonts/truetype/inter/Inter-SemiBold.ttf",
    ],
}

_font_cache = {}
_display_cache = {}
_resolved_path = None
_display_logged = False


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


def display(size, weight="medium"):
    """The Latin display face at `size`, or the body face if it is missing.

    Never called directly for anything that might be Chinese — use `face()`,
    which decides. This is here for text that is digits by construction.
    """
    global _display_logged
    key = (size, weight)
    if key in _display_cache:
        return _display_cache[key]

    for path in _DISPLAY_PATHS.get(weight, ()):
        if not os.path.exists(path):
            continue
        try:
            loaded = ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 - a broken font file is just the next one
            continue
        if not _display_logged:
            _display_logged = True
            logger.info("Panel display face: %s", os.path.dirname(path))
        _display_cache[key] = loaded
        return loaded

    if not _display_logged:
        _display_logged = True
        logger.info("No display face found; headline type falls back to the "
                    "body font. Install fonts-inter for the intended look.")
    fallback = font(size)
    _display_cache[key] = fallback
    return fallback


# Everything from CJK Radicals up. Anything below it, Inter covers — including
# the degree sign and the arrows the pages use.
def _needs_cjk(text):
    return any(ord(ch) >= 0x2E80 for ch in str(text))


def face(text, size, weight="medium"):
    """The right face for this string.

    The display face unless the string needs Chinese, in which case the body
    font — which is why a frame set to Chinese still renders its weather
    description, rather than a row of empty boxes in a prettier typeface.
    """
    return font(size) if _needs_cjk(text) else display(size, weight)


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
    # A city name can be Chinese; "Updated 14:05" never is.
    title_font = face(title, title_size, "medium")
    baseline = box.bottom - 10

    reserved = 0
    if right_text:
        right_font = face(right_text, right_size, "regular")
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
    body = face(message, 20, "medium")
    draw.text((box.cx, box.cy - (12 if hint else 0)), fit(message, body, box.w - 24),
              fill=BLACK, font=body, anchor="mm")
    if hint:
        small = face(hint, 14, "regular")
        draw.text((box.cx, box.cy + 16), fit(hint, small, box.w - 24),
                  fill=BLUE, font=small, anchor="mm")


# ── Weather icons ────────────────────────────────────────────────────────
#
# Drawn as vectors rather than glyphs. The old code mapped conditions to emoji
# (☀ 🌧 ⛈), and neither DejaVu nor WenQuanYi has those code points — every one
# of them would have come out as an empty box. These also scale to any size
# and land on exact palette colours.

# ── Weather icons ────────────────────────────────────────────────────────
#
# The set follows four rules, and every icon in it obeys all four. They are
# what make a set look like a set rather than like a pile of drawings:
#
#   1. One stroke weight for everything. The cloud, a raindrop, a sun ray and
#      the bolt are the same line. Nothing is filled — the bolt used to be a
#      solid polygon among outlines, which is exactly the sort of thing that
#      reads as fussy next to its neighbours.
#   2. Every stroke is a path with round caps and round joins.
#   3. Where two shapes overlap, the one in front is knocked out of the one
#      behind by a gap of background before its own stroke, so the two read as
#      separate objects rather than as one tangled outline.
#   4. Nothing decorative. If a line is not carrying meaning it is not drawn.
#
# Drawn four times over size and thresholded back down, because PIL strokes
# are square-ended and unantialiased, and because the panel's driver dithers —
# pure black and white passes through that untouched, grey becomes speckle.

SS = 4
_INK_THRESHOLD = 142
_INK, _PAPER = 0, 255


def _line(size):
    """The one stroke weight, in the icon's own coordinates."""
    return max(SS * 2, round(size * 0.055))


def _gap(size):
    """The knockout between a shape and whatever sits behind it."""
    return _line(size) * 0.85


def _stroke(draw, points, w, closed=False):
    """A path with round caps and joins."""
    pts = list(points) + ([points[0]] if closed else [])
    draw.line(pts, fill=_INK, width=int(w), joint="curve")
    r = w / 2
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=_INK)


def _ring(draw, cx, cy, r, w):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=_INK, width=int(w))


def _cloud_body(draw, cx, cy, size, grow, colour):
    """The cloud's silhouette, inflated by `grow`, filled with `colour`."""
    width = size
    base = cy + width * 0.15
    for dx, dy, r in ((-0.250, -0.045, 0.185),
                      (-0.005, -0.200, 0.260),
                      (+0.245, -0.080, 0.205)):
        px, py, rr = cx + width * dx, base + width * dy, width * r + grow
        draw.ellipse([px - rr, py - rr, px + rr, py + rr], fill=colour)
    draw.rounded_rectangle(
        [cx - width * 0.435 - grow, base - width * 0.060 - grow,
         cx + width * 0.450 + grow, base + width * 0.095 + grow],
        radius=width * 0.085 + grow, fill=colour)


def _cloud(draw, cx, cy, size, knockout=False):
    """A cloud as one continuous outline of the standard weight.

    The outline straddles the silhouette's edge — inflated by half a stroke in
    ink, deflated by half a stroke in paper — so it is a stroke rather than a
    filled blob with a hole in it, and its weight matches every other line.
    """
    w = _line(size)
    if knockout:
        _cloud_body(draw, cx, cy, size, w / 2 + _gap(size), _PAPER)
    _cloud_body(draw, cx, cy, size, w / 2, _INK)
    _cloud_body(draw, cx, cy, size, -w / 2, _PAPER)


def _sun(draw, cx, cy, size, rays=True):
    """An open disc with eight detached rays of the same weight."""
    from math import cos, sin, pi
    w = _line(size)
    r = size * 0.30
    _ring(draw, cx, cy, r, w)
    if not rays:
        return
    inner, outer = r + size * 0.16, r + size * 0.34
    for i in range(8):
        a = i * pi / 4
        _stroke(draw, [(cx + cos(a) * inner, cy + sin(a) * inner),
                       (cx + cos(a) * outer, cy + sin(a) * outer)], w)


def _moon(draw, cx, cy, size):
    """A crescent: one ring with a second disc taken out of it."""
    w = _line(size)
    r = size * 0.34
    _ring(draw, cx, cy, r, w)
    draw.ellipse([cx - r * 1.55 - w, cy - r * 1.20 - w,
                  cx + r * 0.34 - w, cy + r * 1.20 + w], fill=_PAPER)
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=296, end=64,
             fill=_INK, width=int(w))


def _streaks(draw, cx, cy, size, count=3, length=0.30, spacing=0.20):
    """Parallel slanted strokes: rain, and the rain half of sleet."""
    w = _line(size)
    for i in range(count):
        x = cx + (i - (count - 1) / 2) * size * spacing
        _stroke(draw, [(x + size * length * 0.30, cy),
                       (x - size * length * 0.30, cy + size * length)], w)


def _dots(draw, cx, cy, size, count=3, spacing=0.20):
    """Round dots of the stroke's own weight: flurries, drizzle."""
    r = _line(size) * 0.62
    for i in range(count):
        x = cx + (i - (count - 1) / 2) * size * spacing
        y = cy + (size * 0.11 if i % 2 else 0)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=_INK)


def _flake(draw, cx, cy, size):
    """A six-spoke flake with barbs, drawn in the one weight."""
    from math import cos, sin, pi
    w = _line(size)
    r = size * 0.30
    for k in range(3):
        a = k * pi / 3
        dx, dy = cos(a) * r, sin(a) * r
        _stroke(draw, [(cx - dx, cy - dy), (cx + dx, cy + dy)], w)
        # Barbs at each tip, swept back along the spoke. The angle has to be
        # taken from the direction that tip points in, not from the spoke —
        # reusing the spoke's angle sent the far tip's barbs inwards and the
        # flake collapsed into a knot.
        for sign in (1, -1):
            tip = (cx + dx * sign, cy + dy * sign)
            outward = a if sign > 0 else a + pi
            for turn in (outward + pi * 0.78, outward - pi * 0.78):
                _stroke(draw, [tip, (tip[0] + cos(turn) * r * 0.42,
                                     tip[1] + sin(turn) * r * 0.42)], w)


def _bolt(draw, cx, cy, size):
    """One zigzag line. Not a filled polygon among outlines."""
    w = _line(size)
    _stroke(draw, [(cx + size * 0.13, cy - size * 0.30),
                   (cx - size * 0.15, cy + size * 0.05),
                   (cx + size * 0.06, cy + size * 0.05),
                   (cx - size * 0.11, cy + size * 0.34)], w)


def _fog(draw, cx, cy, size, lines=3):
    """Wavy strokes — moving air, where straight lines would read as a grille."""
    from math import sin, pi
    w = _line(size)
    for i in range(lines):
        y = cy + (i - (lines - 1) / 2) * size * 0.26
        half = size * 0.46
        amp = size * 0.055
        points = []
        for step in range(25):
            t = step / 24
            points.append((cx - half + 2 * half * t,
                           y + sin(t * 2 * pi + i * 0.6) * amp))
        _stroke(draw, points, w)


def _paint_icon(draw, size, code):
    """One condition, centred, in ink on a paper-coloured layer."""
    cx = cy = size / 2
    kind = (code or "")[:2]
    night = str(code or "").endswith("n")
    S = size

    if kind == "01":                                        # clear
        (_moon if night else _sun)(draw, cx, cy, S * 0.62)
    elif kind == "02":                                      # a few clouds
        # The luminary is behind; the cloud knocks a gap out of it.
        if night:
            _moon(draw, cx + S * 0.20, cy - S * 0.21, S * 0.46)
        else:
            _sun(draw, cx + S * 0.21, cy - S * 0.21, S * 0.40)
        _cloud(draw, cx - S * 0.06, cy + S * 0.10, S * 0.76, knockout=True)
    elif kind == "03":                                      # scattered
        _cloud(draw, cx, cy, S * 0.84)
    elif kind == "04":                                      # broken
        if size >= 42 * SS:
            _cloud(draw, cx + S * 0.16, cy - S * 0.16, S * 0.54)
            _cloud(draw, cx - S * 0.07, cy + S * 0.08, S * 0.80, knockout=True)
        else:
            _cloud(draw, cx, cy, S * 0.84)
    elif kind == "09":                                      # shower
        _cloud(draw, cx, cy - S * 0.14, S * 0.76)
        _dots(draw, cx, cy + S * 0.30, S, count=4, spacing=0.17)
    elif kind == "10":                                      # rain
        _cloud(draw, cx, cy - S * 0.14, S * 0.76)
        _streaks(draw, cx, cy + S * 0.22, S, count=3, length=0.22, spacing=0.19)
    elif kind == "11":                                      # thunderstorm
        _cloud(draw, cx, cy - S * 0.17, S * 0.76)
        _bolt(draw, cx, cy + S * 0.24, S * 0.72)
    elif kind == "13":                                      # snow
        _cloud(draw, cx, cy - S * 0.16, S * 0.74)
        if size >= 96 * SS:
            _flake(draw, cx, cy + S * 0.29, S * 0.36)
        else:
            _dots(draw, cx, cy + S * 0.28, S, count=3, spacing=0.19)
    elif kind == "50":                                      # mist
        _fog(draw, cx, cy, S * 0.78)
    else:
        _cloud(draw, cx, cy, S * 0.84)


def weather_icon(image, box, code, size=None):
    """Draw an OpenWeatherMap condition icon centred in `box`, onto `image`.

    Takes the image rather than a draw handle: the icon is composited as a
    thresholded mask, not stroked straight onto the page.
    """
    size = int(size or min(box.w, box.h))
    if size < 8:
        return

    layer = Image.new("L", (size * SS, size * SS), _PAPER)
    _paint_icon(ImageDraw.Draw(layer), size * SS, code)
    layer = layer.resize((size, size), Image.LANCZOS)

    mask = layer.point(lambda v: 255 if v < _INK_THRESHOLD else 0)
    image.paste(Image.new("RGB", (size, size), BLACK),
                (int(box.cx - size / 2), int(box.cy - size / 2)), mask)


def flatten_to_palette(image):
    """Snap every pixel to the panel's six colours, with no dithering.

    The driver quantises with Floyd-Steinberg, which is right for a photograph
    and wrong for everything else: antialiased type and hairlines come out as
    fields of speckle. Pages made of text and rules are flattened here first,
    so what reaches the panel is already exactly on-palette and the dither has
    nothing left to do.
    """
    reference = Image.new("P", (1, 1))
    reference.putpalette([c for colour in PALETTE for c in colour] +
                         [0, 0, 0] * (256 - len(PALETTE)))
    return image.convert("RGB").quantize(
        palette=reference, dither=Image.Dither.NONE).convert("RGB")


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
