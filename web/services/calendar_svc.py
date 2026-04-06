"""
Calendar service - fetches events from iCal URL.
"""

import logging
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("vignette.calendar")

CACHE_DURATION = 900  # 15 minutes
_cache = {"data": None, "timestamp": 0}


def fetch_calendar_events(ical_url, days_ahead=14):
    """Fetch upcoming events from an iCal URL."""
    if not ical_url:
        return []

    now = time.time()
    if _cache["data"] is not None and (now - _cache["timestamp"]) < CACHE_DURATION:
        return _cache["data"]

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
        _cache["data"] = events
        _cache["timestamp"] = now
        logger.info(f"Calendar updated: {len(events)} upcoming events")
        return events

    except Exception as e:
        logger.error(f"Calendar fetch failed: {e}", exc_info=True)
        return _cache.get("data") or []


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
                ev_end = event.get("end", start)
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
        elif prop_name == "DTEND":
            event["end"] = _parse_ical_date(prop, value)
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
