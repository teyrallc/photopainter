"""
Calendar service - fetches events from one or more iCal URLs.

A household keeps more than one calendar — work, school, the shared one — so
this takes a list of feeds rather than a single URL. Each event is tagged with
the colour of the feed it came from, which is how the panel tells them apart
with the four inks it has left after the paper and the type.
"""

import logging
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("vignette.calendar")

CACHE_DURATION = 900  # 15 minutes
# Keyed by URL: one slow or broken feed must not evict another's listing, and
# a feed that fails now keeps showing what it last said rather than blanking
# the whole page.
_cache = {}

# More than any panel can show, but a bound all the same — a runaway feed must
# not be able to fill the Pi's memory with events nothing will ever draw.
MAX_MERGED_EVENTS = 60


def fetch_calendar_events(calendars, days_ahead=14):
    """Upcoming events across every subscribed feed, soonest first.

    Takes the config's `calendars` list. A bare URL string is also accepted,
    because the setup page and older callers have one of those.
    """
    if isinstance(calendars, str):
        calendars = [{"url": calendars}] if calendars.strip() else []
    feeds = [c for c in (calendars or [])
             if isinstance(c, dict) and str(c.get("url") or "").strip()]
    if not feeds:
        return []

    merged = []
    for feed in feeds:
        url = feed["url"].strip()
        name, events = _fetch_one(url, days_ahead)
        label = str(feed.get("name") or "").strip() or name
        color = feed.get("color") or "blue"
        for event in events:
            # Copied rather than mutated: the cache holds these, and tagging
            # in place would leave one feed's colour on another's cached
            # events if the same URL were subscribed twice.
            merged.append(dict(event, calendar=label, color=color))

    merged.sort(key=lambda e: e["start"])
    return merged[:MAX_MERGED_EVENTS]


def _fetch_one(ical_url, days_ahead):
    """One feed's (name, events), from the network or from the cache."""
    now = time.time()
    entry = _cache.get(ical_url)
    if entry and (now - entry["timestamp"]) < CACHE_DURATION:
        return entry["name"], entry["events"]

    try:
        req = urllib.request.Request(ical_url, headers={"User-Agent": "Vignette/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            # Try UTF-8 first, fallback to latin-1
            try:
                ical_text = raw.decode("utf-8")
            except UnicodeDecodeError:
                ical_text = raw.decode("latin-1")

        events = _parse_ical(ical_text, days_ahead)
        name = _calendar_name(ical_text)
        _cache[ical_url] = {"events": events, "name": name, "timestamp": now}
        logger.info(f"Calendar updated: {len(events)} upcoming events "
                    f"from {name or ical_url}")
        return name, events

    except Exception as e:
        logger.error(f"Calendar fetch failed: {e}", exc_info=True)
        # Stale is better than empty on a frame on a wall: a flaky feed would
        # otherwise wipe today's events off the panel until it recovered.
        return (entry or {}).get("name", ""), (entry or {}).get("events", [])


def _calendar_name(text):
    """The feed's own name, so a calendar can label itself in Settings."""
    match = re.search(r"^X-WR-CALNAME[^:]*:(.+)$", text or "",
                      re.IGNORECASE | re.MULTILINE)
    return _decode_ical_text(match.group(1).strip())[:40] if match else ""


def forget_calendars():
    """Drop every cached feed so the next read is live."""
    _cache.clear()


def _unfold_ical(text):
    """Unfold iCal continuation lines (RFC 5545: lines starting with space/tab)."""
    lines = []
    for line in text.splitlines():
        if line and line[0] in (' ', '\t'):
            # Continuation of previous line
            if lines:
                lines[-1] += line[1:]
            else:
                lines.append(line[1:])
        else:
            lines.append(line)
    return lines


def _decode_ical_text(text):
    """Decode iCal escaped text (backslash sequences)."""
    if not text:
        return text
    text = text.replace("\\n", "\n").replace("\\,", ",")
    text = text.replace("\\;", ";").replace("\\\\", "\\")
    return text


def _parse_ical(text, days_ahead):
    """Parse iCal text and extract VEVENT blocks."""
    events = []
    now = datetime.now()
    end = now + timedelta(days=days_ahead)

    lines = _unfold_ical(text)

    in_event = False
    event = {}

    for line in lines:
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            event = {}
        elif line == "END:VEVENT":
            in_event = False
            if event.get("start"):
                start = event["start"]
                ev_end = event.get("end")
                if ev_end is None:
                    # An all-day event with no DTEND covers the whole day, not
                    # the midnight it parsed to — without this it drops off the
                    # frame at 00:01 on the very day it is happening.
                    ev_end = (start + timedelta(days=1)
                              if event.get("all_day") else start)
                # Include if event overlaps with [now, end]
                if start <= end and ev_end >= now:
                    events.append(event)
            continue
        elif not in_event:
            continue

        # Parse property
        prop, value = _split_ical_line(line)
        if prop is None:
            continue

        prop_name = prop.split(";")[0].upper()

        if prop_name == "SUMMARY":
            event["summary"] = _decode_ical_text(value)
        elif prop_name == "DTSTART":
            event["start"] = _parse_ical_date(prop, value)
            event["all_day"] = _is_date_only(prop, value)
        elif prop_name == "DTEND":
            event["end"] = _parse_ical_date(prop, value)
        elif prop_name == "COLOR":
            # RFC 7986: a CSS3 colour name for this one event, independent of
            # whatever colour its calendar is drawn in.
            event["event_color"] = value.strip()[:32]
        elif prop_name == "LOCATION":
            event["location"] = _decode_ical_text(value)
        elif prop_name == "DESCRIPTION":
            event["description"] = _decode_ical_text(value)

    events.sort(key=lambda e: e.get("start", now))
    return events[:15]


def _split_ical_line(line):
    """Split an iCal line into property (with params) and value."""
    # Format: PROP;PARAM=VAL:value  or  PROP:value
    idx = line.find(":")
    if idx < 0:
        return None, None
    return line[:idx], line[idx + 1:]


def _is_date_only(prop, datestr):
    """Whether a DTSTART names a day rather than a moment.

    An all-day event is published as ``DTSTART;VALUE=DATE:20260818`` — there
    is no time in it at all. Parsing that lands on midnight, which is a real
    instant and reads on the panel as "Today 00:00": a dentist appointment
    apparently booked for the stroke of twelve. The flag travels with the
    event so whoever draws it can say "All day" instead of inventing an hour.

    The parameter is what RFC 5545 requires, and the eight-digit shape is what
    it means; either is accepted, since a date-time value always carries a T.
    """
    if re.search(r"VALUE=DATE(?![-A-Za-z])", prop or "", re.IGNORECASE):
        return True
    return bool(re.fullmatch(r"\d{8}", (datestr or "").strip()))


def _parse_ical_date(prop, datestr):
    """Parse iCal date from property line and value.

    Handles:
      DTSTART:20260405T100000Z       (UTC)
      DTSTART:20260405T100000        (local/floating)
      DTSTART;VALUE=DATE:20260405    (all-day)
      DTSTART;TZID=Asia/Taipei:20260405T100000  (timezone)
    """
    datestr = datestr.strip()
    if not datestr:
        return None

    # Check for TZID parameter
    tzid = None
    if "TZID=" in prop.upper():
        m = re.search(r'TZID=([^;:]+)', prop, re.IGNORECASE)
        if m:
            tzid = m.group(1)

    # Try various formats
    for fmt in [
        "%Y%m%dT%H%M%SZ",   # UTC
        "%Y%m%dT%H%M%S",     # Local or TZID
        "%Y%m%d",             # All-day
    ]:
        try:
            dt = datetime.strptime(datestr, fmt)
            # If UTC format (ends with Z), convert to local
            if fmt.endswith("Z"):
                dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
            return dt
        except ValueError:
            continue

    logger.warning(f"Could not parse date: {datestr}")
    return None


def get_today_info():
    """Get today's date info for calendar display."""
    now = datetime.now()
    weekdays_en = ["Monday", "Tuesday", "Wednesday", "Thursday",
                   "Friday", "Saturday", "Sunday"]
    return {
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "weekday": weekdays_en[now.weekday()],
        "weekday_en": now.strftime("%A"),
        "date_str": now.strftime("%Y/%m/%d"),
        "time_str": now.strftime("%H:%M"),
    }
