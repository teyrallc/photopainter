#!/usr/bin/env python3
"""Tests for the calendar: several feeds, and telling them apart by colour.

A household keeps more than one calendar, and the panel has four inks left
after the paper and the type. What is checked here is that the list survives
every way it can be written, that one broken feed cannot take the others down
with it, and that a colour reaching the screen is one the display can print.

No network: urlopen is replaced with canned answers.

    python3 -m pytest tests/
    python3 tests/test_calendar.py     # also runs standalone
"""

import io
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO, "web") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "web"))

from services import calendar_svc, epd_ui as ui, renderer  # noqa: E402
from services.config import Config, CALENDAR_COLORS, normalize_calendars  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────

def _ics(summary, *, hours=1, name="", color=None, all_day=False):
    # Relative to now, never an absolute hour: an event at 09:00 is in the past
    # by mid-morning and the parser correctly drops it, which made these tests
    # pass or fail depending on what time of day they were run.
    when = datetime.now() + timedelta(hours=hours)
    if all_day:
        start = (f"DTSTART;VALUE=DATE:{when.strftime('%Y%m%d')}\r\n"
                 f"DTEND;VALUE=DATE:{(when + timedelta(days=1)).strftime('%Y%m%d')}")
    else:
        stamp = when.strftime("%Y%m%dT%H%M%S")
        start = f"DTSTART:{stamp}\r\nDTEND:{stamp}"
    return ("BEGIN:VCALENDAR\r\n"
            + (f"X-WR-CALNAME:{name}\r\n" if name else "")
            + "BEGIN:VEVENT\r\n"
            + f"SUMMARY:{summary}\r\n"
            + (f"COLOR:{color}\r\n" if color else "")
            + start + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


class _Feeds:
    """Answers each URL with its canned iCal, and counts the requests."""

    def __init__(self, bodies):
        self.bodies = bodies
        self.hits = {}

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.hits[url] = self.hits.get(url, 0) + 1
        body = self.bodies.get(url)
        if body is None:
            raise OSError(f"no such feed: {url}")
        return _Response(body.encode("utf-8"))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _with_feeds(feeds, fn):
    calendar_svc.forget_calendars()
    real = urllib.request.urlopen
    urllib.request.urlopen = feeds
    try:
        return fn()
    finally:
        urllib.request.urlopen = real
        calendar_svc.forget_calendars()


# ── The list of feeds ────────────────────────────────────────────────────

def test_the_single_url_setting_becomes_the_first_calendar():
    """A frame already on a wall has one iCal URL and no list."""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "config.json")
        with open(path, "w") as handle:
            json.dump({"calendar_ical_url": "https://old.example/a.ics"}, handle)

        config = Config(path)
        assert config.get("calendars") == [
            {"url": "https://old.example/a.ics", "name": "", "color": "blue"}]
        # And the old key still answers, for anything that has not been moved.
        assert config.get("calendar_ical_url") == "https://old.example/a.ics"


def test_deleting_the_first_calendar_does_not_bring_it_back():
    """The mirror flows one way, and this is why.

    `calendar_ical_url` shadows the first calendar. Reconciling in both
    directions looks tidier and resurrects a feed the moment you delete it:
    the old URL left sitting in the mirror is indistinguishable from someone
    having just typed it.
    """
    with tempfile.TemporaryDirectory() as directory:
        config = Config(os.path.join(directory, "config.json"))
        config.update({"calendars": [
            {"url": "https://a.example/a.ics", "color": "blue"},
            {"url": "https://b.example/b.ics", "color": "red"},
        ]})
        assert config.get("calendar_ical_url") == "https://a.example/a.ics"

        config.update({"calendars": [{"url": "https://b.example/b.ics",
                                      "color": "red"}]})
        urls = [c["url"] for c in config.get("calendars")]
        assert urls == ["https://b.example/b.ics"], urls
        assert config.get("calendar_ical_url") == "https://b.example/b.ics"

        # Emptying it empties both, and it stays empty across a reload.
        config.update({"calendars": []})
        assert config.get("calendars") == []
        assert config.get("calendar_ical_url") == ""
        assert Config(config.config_path).get("calendars") == []


def test_writing_the_old_key_alone_edits_the_first_calendar():
    """The setup page knows one URL and nothing about a list."""
    with tempfile.TemporaryDirectory() as directory:
        config = Config(os.path.join(directory, "config.json"))
        config.update({"calendars": [
            {"url": "https://a.example/a.ics", "name": "Work", "color": "blue"},
            {"url": "https://b.example/b.ics", "name": "Home", "color": "red"},
        ]})
        config.update({"calendar_ical_url": "https://new.example/n.ics"})

        entries = config.get("calendars")
        assert [c["url"] for c in entries] == ["https://new.example/n.ics",
                                               "https://b.example/b.ics"]
        # The rest of the first calendar survives being re-pointed.
        assert entries[0]["name"] == "Work"
        assert entries[0]["color"] == "blue"
        # And the second is untouched.
        assert entries[1]["name"] == "Home"


def test_a_colour_the_panel_cannot_print_is_replaced():
    """config.json is a file on a disk somebody can edit."""
    entries = normalize_calendars([
        {"url": "https://a.example/a.ics", "color": "chartreuse"},
        {"url": "https://b.example/b.ics", "color": "BLUE"},
        {"url": "https://c.example/c.ics"},
        {"url": "   ", "color": "red"},          # no URL: not a calendar
        "https://d.example/d.ics",               # a bare string still counts
        {"name": "no url either"},
        "not a dict or a url either",             # nor is prose a URL
        {"url": "file:///etc/shadow"},            # urlopen would open this
        {"url": "ftp://example.com/a.ics"},
    ])
    assert [c["url"] for c in entries] == [
        "https://a.example/a.ics", "https://b.example/b.ics",
        "https://c.example/c.ics", "https://d.example/d.ics"]
    assert all(c["color"] in CALENDAR_COLORS for c in entries)
    # An explicit, printable colour is kept as given.
    assert entries[1]["color"] == "blue"
    # The invalid ones are filled from what is still free, not all with one.
    assert len({c["color"] for c in entries}) == 4


# ── Fetching several feeds ───────────────────────────────────────────────

def test_events_are_merged_and_tagged_with_their_calendar():
    feeds = _Feeds({
        "https://work.example/w.ics": _ics("Standup", hours=25, name="Work"),
        "https://home.example/h.ics": _ics("Swimming", hours=2),
    })
    events = _with_feeds(feeds, lambda: calendar_svc.fetch_calendar_events([
        {"url": "https://work.example/w.ics", "name": "", "color": "blue"},
        {"url": "https://home.example/h.ics", "name": "Home", "color": "green"},
    ]))

    assert [e["summary"] for e in events] == ["Swimming", "Standup"], "not merged in time order"
    tagged = {e["summary"]: e for e in events}
    assert tagged["Standup"]["color"] == "blue"
    assert tagged["Swimming"]["color"] == "green"
    # A feed with no name of its own in Settings falls back to its own.
    assert tagged["Standup"]["calendar"] == "Work"
    # And one that was named in Settings keeps that name.
    assert tagged["Swimming"]["calendar"] == "Home"


def test_one_broken_feed_does_not_empty_the_others():
    """A frame on a wall must not go blank because one server is down."""
    feeds = _Feeds({"https://good.example/g.ics": _ics("Dinner", hours=3)})
    subscribed = [{"url": "https://good.example/g.ics", "color": "blue"},
                  {"url": "https://gone.example/x.ics", "color": "red"}]

    events = _with_feeds(feeds, lambda: calendar_svc.fetch_calendar_events(subscribed))
    assert [e["summary"] for e in events] == ["Dinner"]


def test_a_feed_that_fails_keeps_showing_what_it_last_said():
    feeds = _Feeds({"https://flaky.example/f.ics": _ics("Dinner", hours=3)})
    subscribed = [{"url": "https://flaky.example/f.ics", "color": "blue"}]

    def both_reads():
        first = calendar_svc.fetch_calendar_events(subscribed)
        # Expire the cache, then break the feed.
        for entry in calendar_svc._cache.values():
            entry["timestamp"] = 0
        feeds.bodies.clear()
        return first, calendar_svc.fetch_calendar_events(subscribed)

    first, second = _with_feeds(feeds, both_reads)
    assert [e["summary"] for e in first] == ["Dinner"]
    assert [e["summary"] for e in second] == ["Dinner"], "a flaky feed blanked the panel"


def test_each_feed_is_cached_on_its_own():
    """One slow feed must not evict another's listing."""
    feeds = _Feeds({"https://a.example/a.ics": _ics("A", hours=1),
                    "https://b.example/b.ics": _ics("B", hours=2)})
    subscribed = [{"url": "https://a.example/a.ics", "color": "blue"},
                  {"url": "https://b.example/b.ics", "color": "red"}]

    def twice():
        calendar_svc.fetch_calendar_events(subscribed)
        return calendar_svc.fetch_calendar_events(subscribed)

    events = _with_feeds(feeds, twice)
    assert len(events) == 2
    assert feeds.hits == {"https://a.example/a.ics": 1,
                          "https://b.example/b.ics": 1}, feeds.hits


def test_a_bare_url_is_still_accepted():
    """The setup page and older callers hand over one string."""
    feeds = _Feeds({"https://one.example/o.ics": _ics("Only", hours=1)})
    events = _with_feeds(
        feeds, lambda: calendar_svc.fetch_calendar_events("https://one.example/o.ics"))
    assert [e["summary"] for e in events] == ["Only"]
    assert events[0]["color"] == "blue"

    assert calendar_svc.fetch_calendar_events("") == []
    assert calendar_svc.fetch_calendar_events([]) == []
    assert calendar_svc.fetch_calendar_events(None) == []


def test_the_panel_reads_the_calendar_live_and_the_browser_does_not():
    """A repaint decides what stands on the wall for the next hour.

    The cache is there so a dashboard being polled does not pull somebody's
    feed on every poll. It must not be there when the panel is being drawn:
    the events shown are then up to a quarter of an hour out of date at the one
    moment they are chosen.
    """
    feeds = _Feeds({"https://a.example/a.ics": _ics("A", hours=1)})
    subscribed = [{"url": "https://a.example/a.ics", "color": "blue"}]

    def reads():
        calendar_svc.fetch_calendar_events(subscribed)                  # cold
        calendar_svc.fetch_calendar_events(subscribed)                  # cached
        cached = dict(feeds.hits)
        calendar_svc.fetch_calendar_events(subscribed, refresh=True)    # live
        return cached, dict(feeds.hits)

    cached, live = _with_feeds(feeds, reads)
    assert cached == {"https://a.example/a.ics": 1}, cached
    assert live == {"https://a.example/a.ics": 2}, live


def test_the_photo_page_does_not_pull_anybody_s_calendar():
    """A slideshow drawing pictures has no calendar on it to be stale.

    Fetching before deciding which page to draw had a five-minute slideshow
    reading the feed twelve times an hour for nothing.
    """
    source = open(os.path.join(REPO, "web", "services", "display_mgr.py"),
                  encoding="utf-8").read()
    body = source[source.index("def display_current_page"):]
    fetch = body.index("fetch_calendar_events")
    guard = body.index('page in ("home", "widget")')
    assert guard < fetch, "the calendar is fetched before the page is known"
    assert "refresh=True" in body[fetch:fetch + 120], (
        "the panel is drawing from the browser's cache")


# ── Colour on the panel ──────────────────────────────────────────────────

def test_any_css_colour_lands_on_an_ink_the_panel_has():
    """Google's own event palette, and the shapes RFC 7986 allows.

    Matching on hue rather than on RGB distance is the point of the exercise:
    salmon sits marginally closer to yellow than to red measured straight in
    RGB, which is not what anybody looking at it sees.
    """
    assert ui.nearest_calendar_ink("salmon") == ui.RED
    assert ui.nearest_calendar_ink("tomato") == ui.RED

    google = {"#e67c73": ui.RED, "#f4511e": ui.RED, "#f6bf26": ui.YELLOW,
              "#33b679": ui.GREEN, "#0b8043": ui.GREEN, "#039be5": ui.BLUE,
              "#3f51b5": ui.BLUE, "#7986cb": ui.BLUE}
    for hexcode, ink in google.items():
        assert ui.nearest_calendar_ink(hexcode) == ink, hexcode
    assert ui.nearest_calendar_ink("#f00") == ui.RED, "short hex"

    # Nothing to match on: grey has no hue, and neither has a made-up name.
    for nothing in ("", None, "  ", "#616161", "white", "black",
                    "flamingo", "#gggggg", "#12345"):
        assert ui.nearest_calendar_ink(nothing) is None, nothing
    assert ui.nearest_calendar_ink("", default=ui.BLUE) == ui.BLUE

    # And every colour a calendar may be set to is one of the four.
    for name in CALENDAR_COLORS:
        assert ui.calendar_ink(name) in ui.CALENDAR_INK.values()


def test_yellow_is_a_tick_but_never_type():
    """Yellow ink on white paper is clear as a bar and a smudge as words."""
    assert ui.calendar_ink("yellow") == ui.YELLOW
    assert ui.calendar_text_ink("yellow") == ui.BLACK
    for other in ("blue", "red", "green"):
        assert ui.calendar_text_ink(other) == ui.calendar_ink(other), other


def test_each_calendar_draws_its_agenda_row_in_its_own_colour():
    from PIL import Image, ImageDraw

    now = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)

    def ticks(color):
        img = Image.new("RGB", (300, 120), (255, 255, 255))
        renderer._draw_agenda(
            ImageDraw.Draw(img), ui.Box(0, 0, 280, 100),
            [{"summary": "x", "start": now + timedelta(hours=1), "color": color}])
        px = img.load()
        return {px[1, y] for y in range(100)} - {(255, 255, 255)}

    assert ticks("blue") == {ui.BLUE}
    assert ticks("red") == {ui.RED}
    assert ticks("green") == {ui.GREEN}
    assert ticks("yellow") == {ui.YELLOW}


def test_an_event_with_its_own_colour_splits_the_tick():
    """Whose calendar on top, which kind of thing underneath."""
    from PIL import Image, ImageDraw

    now = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)

    def tick_colours(event):
        img = Image.new("RGB", (300, 120), (255, 255, 255))
        renderer._draw_agenda(ImageDraw.Draw(img), ui.Box(0, 0, 280, 100), [event])
        px = img.load()
        seen = []
        for y in range(100):
            if px[1, y] != (255, 255, 255) and px[1, y] not in seen:
                seen.append(px[1, y])
        return seen

    base = {"summary": "x", "start": now + timedelta(hours=1), "color": "green"}

    # Top half the calendar's, bottom half the event's.
    assert tick_colours(dict(base, event_color="tomato")) == [ui.GREEN, ui.RED]

    # No colour of its own, or the same one: one solid tick, not two halves.
    assert tick_colours(base) == [ui.GREEN]
    assert tick_colours(dict(base, event_color="forestgreen")) == [ui.GREEN]
    # A colour with no hue to match on is not a colour: still solid.
    assert tick_colours(dict(base, event_color="#616161")) == [ui.GREEN]


def test_a_day_carrying_two_calendars_gets_two_dots():
    """The month grid says which feeds a day belongs to, not just that it does."""
    now = datetime.now().replace(day=15, hour=9, minute=0, second=0, microsecond=0)

    def at(day, color):
        return {"summary": "x", "start": now.replace(day=day), "color": color}

    assert now.day == 15, "the marked days below must not be today"
    days = renderer._event_days(
        [at(3, "blue"), at(3, "red"), at(3, "blue"),      # duplicate: one dot
         at(9, "green"),
         at(21, "blue"), at(21, "red"), at(21, "green"), at(21, "yellow")],
        now)

    assert days[3] == [ui.BLUE, ui.RED], "a repeated calendar drew twice"
    assert days[9] == [ui.GREEN]
    # Four feeds on one day: the grid holds three, and says so by holding three
    # rather than by drawing them on top of each other.
    assert len(days[21]) == 4
    assert renderer.MAX_DAY_DOTS == 3

    # An event in another month is not this month's business.
    other = renderer._event_days([{"summary": "x",
                                   "start": now + timedelta(days=60)}], now)
    assert other == {}


def test_the_dots_stay_centred_under_the_day_however_many_there_are():
    """One dot or three, the group hangs off the same axis as the numeral."""
    from PIL import Image, ImageDraw

    now = datetime.now().replace(day=15, hour=9, minute=0, second=0, microsecond=0)

    def dot_centre(colors):
        img = Image.new("RGB", (420, 300), (255, 255, 255))
        # Not today: today's cell draws the red circle and no dots at all.
        marked = now.replace(day=now.day + 2)
        events = [{"summary": "x", "start": marked, "color": c} for c in colors]
        renderer._draw_month_grid(ImageDraw.Draw(img), ui.Box(0, 0, 400, 280),
                                  now, events)
        px = img.load()
        xs = [x for x in range(420) for y in range(300)
              if px[x, y] in (ui.BLUE, ui.GREEN) and px[x, y] != (255, 255, 255)]
        return (min(xs) + max(xs)) / 2 if xs else None

    one = dot_centre(["blue"])
    three = dot_centre(["blue", "green", "blue"])
    assert one is not None and three is not None
    assert abs(one - three) <= 1.0, (one, three)


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}\n        {exc}")
        except Exception as exc:  # noqa: BLE001 - report, don't mask
            failed += 1
            print(f"  ERROR {name}\n        {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
