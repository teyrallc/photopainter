"""
Calendar service - fetches events from iCal URL.
"""

import logging
import time
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger("vignette.calendar")

CACHE_DURATION = 900  # 15 minutes
_cache = {"data": None, "timestamp": 0}


def fetch_calendar_events(ical_url, days_ahead=7):
    """Fetch upcoming events from an iCal URL."""
    if not ical_url:
        return []

    now = time.time()
    if _cache["data"] is not None and (now - _cache["timestamp"]) < CACHE_DURATION:
        return _cache["data"]

    try:
        req = urllib.request.Request(ical_url, headers={"User-Agent": "Vignette/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            ical_text = resp.read().decode("utf-8", errors="replace")

        events = _parse_ical(ical_text, days_ahead)
        _cache["data"] = events
        _cache["timestamp"] = now
        logger.info(f"Calendar updated: {len(events)} upcoming events")
        return events

    except Exception as e:
        logger.error(f"Calendar fetch failed: {e}")
        return _cache.get("data") or []


def _parse_ical(text, days_ahead):
    """Simple iCal parser - extracts VEVENT blocks."""
    events = []
    now = datetime.now()
    end = now + timedelta(days=days_ahead)

    in_event = False
    event = {}

    for line in text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            event = {}
        elif line == "END:VEVENT":
            in_event = False
            if event.get("start"):
                # Filter to upcoming events only
                if event["start"] >= now and event["start"] <= end:
                    events.append(event)
                elif event.get("end") and event["end"] >= now and event["start"] <= end:
                    events.append(event)
        elif in_event:
            if line.startswith("SUMMARY:"):
                event["summary"] = line[8:]
            elif line.startswith("DTSTART"):
                event["start"] = _parse_ical_date(line)
            elif line.startswith("DTEND"):
                event["end"] = _parse_ical_date(line)
            elif line.startswith("LOCATION:"):
                event["location"] = line[9:]

    events.sort(key=lambda e: e.get("start", now))
    return events[:10]  # Max 10 upcoming events


def _parse_ical_date(line):
    """Parse iCal date formats."""
    # Handle both DTSTART:20260405T100000Z and DTSTART;VALUE=DATE:20260405
    parts = line.split(":", 1)
    if len(parts) < 2:
        return None

    datestr = parts[1].strip()
    for fmt in [
        "%Y%m%dT%H%M%SZ",
        "%Y%m%dT%H%M%S",
        "%Y%m%d",
    ]:
        try:
            return datetime.strptime(datestr, fmt)
        except ValueError:
            continue
    return None


def get_today_info():
    """Get today's date info for calendar display."""
    now = datetime.now()
    weekdays_zh = ["一", "二", "三", "四", "五", "六", "日"]
    return {
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "weekday": weekdays_zh[now.weekday()],
        "weekday_en": now.strftime("%A"),
        "date_str": now.strftime("%Y/%m/%d"),
        "time_str": now.strftime("%H:%M"),
    }
