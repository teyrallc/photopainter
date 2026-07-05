"""
Calendar service - fetches events from iCal URL.

Handles timezones (TZID / UTC / floating), all-day events, and best-effort
expansion of simple recurring (RRULE) events so an established weekly meeting
still shows up under "upcoming".
"""

import calendar as _calendar
import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

logger = logging.getLogger("vignette.calendar")

CACHE_DURATION = 900  # 15 minutes
# Cache keyed by (url, days_ahead) so changing either doesn't serve stale data.
_cache = {}
_cache_lock = threading.Lock()


def fetch_calendar_events(ical_url, days_ahead=14):
    """Fetch upcoming events from an iCal URL."""
    if not ical_url:
        return []

    cache_key = (ical_url, days_ahead)
    now = time.time()
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and (now - entry["timestamp"]) < CACHE_DURATION:
            return entry["data"]

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
        with _cache_lock:
            _cache[cache_key] = {"data": events, "timestamp": now}
        logger.info(f"Calendar updated: {len(events)} upcoming events")
        return events

    except Exception as e:
        logger.error(f"Calendar fetch failed: {e}", exc_info=True)
        return (entry["data"] if entry else [])


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
            _finalize_event(event, events, now, end)
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
            dt, all_day = _parse_ical_date(prop, value)
            event["start"] = dt
            event["all_day"] = all_day
        elif prop_name == "DTEND":
            dt, _ = _parse_ical_date(prop, value)
            event["end"] = dt
        elif prop_name == "RRULE":
            event["rrule"] = value.strip()
        elif prop_name == "LOCATION":
            event["location"] = _decode_ical_text(value)
        elif prop_name == "DESCRIPTION":
            event["description"] = _decode_ical_text(value)

    events.sort(key=lambda e: e.get("start", now))
    return events[:15]


def _finalize_event(event, events, now, end):
    """Add an event (or its recurrences) to the list if it overlaps the window."""
    start = event.get("start")
    if not start:
        return

    # All-day event with no DTEND spans the whole day (RFC 5545 DTEND exclusive).
    if event.get("all_day") and "end" not in event:
        event["end"] = start + timedelta(days=1)

    rrule = event.get("rrule")
    if rrule:
        expanded = _expand_rrule(event, rrule, now, end)
        if expanded:
            events.extend(expanded)
            return
        # Unsupported RRULE — fall through and keep the base instance if in-window.

    ev_end = event.get("end", start)
    if start <= end and ev_end >= now:
        events.append(event)


def _split_ical_line(line):
    """Split an iCal line into property (with params) and value."""
    # Format: PROP;PARAM=VAL:value  or  PROP:value
    idx = line.find(":")
    if idx < 0:
        return None, None
    return line[:idx], line[idx + 1:]


def _parse_ical_date(prop, datestr):
    """Parse an iCal date. Returns (datetime_or_None, is_all_day).

    Handles:
      DTSTART:20260405T100000Z                     (UTC)
      DTSTART:20260405T100000                       (local/floating)
      DTSTART;VALUE=DATE:20260405                   (all-day)
      DTSTART;TZID=Asia/Taipei:20260405T100000      (timezone)
    All results are returned as naive *local* datetimes so they compare cleanly
    against datetime.now().
    """
    datestr = datestr.strip()
    if not datestr:
        return None, False

    tzid = None
    if "TZID=" in prop.upper():
        m = re.search(r'TZID=([^;:]+)', prop, re.IGNORECASE)
        if m:
            tzid = m.group(1)

    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            dt = datetime.strptime(datestr, fmt)
        except ValueError:
            continue
        all_day = (fmt == "%Y%m%d")
        if fmt.endswith("Z"):
            # UTC → local naive
            dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        elif tzid and not all_day and ZoneInfo is not None:
            # Interpret in its own timezone, then convert to local naive.
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone().replace(tzinfo=None)
            except Exception:
                logger.warning(f"Unknown TZID '{tzid}'; treating time as local")
        return dt, all_day

    logger.warning(f"Could not parse date: {datestr}")
    return None, False


def _add_months(dt, months):
    m = dt.month - 1 + months
    y = dt.year + m // 12
    m = m % 12 + 1
    day = min(dt.day, _calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=day)


def _expand_rrule(event, rrule_str, window_start, window_end):
    """Best-effort RRULE expansion within [window_start, window_end].

    Supports FREQ=DAILY/WEEKLY/MONTHLY with INTERVAL, COUNT and UNTIL — which
    covers the overwhelming majority of real recurring events. Anything more
    exotic (BYDAY lists, BYSETPOS, etc.) returns [] so the caller keeps the base
    instance instead of silently dropping it.
    """
    rules = {}
    for part in rrule_str.split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            rules[k.strip().upper()] = v.strip()

    freq = rules.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY"):
        return []

    try:
        interval = max(1, int(rules.get("INTERVAL", "1")))
    except ValueError:
        interval = 1

    count = None
    if rules.get("COUNT", "").isdigit():
        count = int(rules["COUNT"])

    until = None
    u = rules.get("UNTIL")
    if u:
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
            try:
                until = datetime.strptime(u, fmt)
                if fmt.endswith("Z"):
                    until = until.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
                break
            except ValueError:
                continue

    start = event["start"]
    duration = (event.get("end") or start) - start

    # Fast-forward the first candidate to near the window so the iteration cap
    # covers the visible range, not a possibly years-long historical span (an
    # established DAILY event started >1000 days ago would otherwise exhaust the
    # cap in the past and never reach 'now'). Only safe when COUNT is absent —
    # COUNT limits total occurrences counted from DTSTART.
    cur = start
    if count is None and cur < window_start:
        if freq == "DAILY":
            skip = (window_start - cur).days // interval
            cur = cur + timedelta(days=skip * interval)
        elif freq == "WEEKLY":
            skip = (window_start - cur).days // (7 * interval)
            cur = cur + timedelta(weeks=skip * interval)
        # MONTHLY: ~1000 months is 80+ years, comfortably within the cap.

    occurrences = []
    n = 0
    for _ in range(1000):  # hard cap against pathological rules
        if count is not None and n >= count:
            break
        if until is not None and cur > until:
            break
        if cur > window_end:
            break
        occ_end = cur + duration
        if occ_end >= window_start and cur <= window_end:
            occ = dict(event)
            occ.pop("rrule", None)
            occ["start"] = cur
            occ["end"] = occ_end
            occurrences.append(occ)
        if freq == "DAILY":
            cur = cur + timedelta(days=interval)
        elif freq == "WEEKLY":
            cur = cur + timedelta(weeks=interval)
        else:  # MONTHLY
            cur = _add_months(cur, interval)
        n += 1
    return occurrences


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
