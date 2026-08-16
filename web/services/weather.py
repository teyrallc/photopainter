"""
Weather service using OpenWeatherMap.

The cache used to be a single module-level slot keyed on nothing but time, so
changing the city in Settings did not change what came back: for the next hour
every caller — the Settings "Test" button, the panel, the dashboard preview —
was handed the *previous* city's reading. Worse, every failure path returned
that same stale entry, so a city OpenWeatherMap does not recognise silently
kept showing the old one instead of saying so.

Both are fixed here: the cache is keyed by the whole query (key, city, units,
language), and a stale entry is only ever reused for the query that produced
it. Anything the owner can act on — no key, no such place, a rejected key, no
internet — is raised as WeatherError so the interface can print the reason.
"""

import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("vignette.weather")

GEO_API = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_API = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_API = "https://api.openweathermap.org/data/2.5/forecast"

USER_AGENT = "Vignette/1.0"
HTTP_TIMEOUT = 10

# Cache weather data for just under an hour (matches the panel refresh loop).
CACHE_DURATION = 3500
# A frame only ever watches one or two places; the bound is here so a caller
# looping over cities cannot grow this without limit.
MAX_CACHE_ENTRIES = 8

_cache = {}          # query key -> {"data": …, "timestamp": …}
_cache_lock = threading.Lock()

UNITS = ("metric", "imperial", "standard")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# "25.03, 121.56" — coordinates always beat a name, and they are the escape
# hatch when OpenWeatherMap's gazetteer does not know a place by the name the
# owner calls it.
_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,/ ]\s*(-?\d+(?:\.\d+)?)\s*$")


class WeatherError(Exception):
    """A failure with a cause the owner can do something about.

    `status` carries the HTTP status when the failure came from a response, so
    callers can map it onto their own (404 for an unknown place, and so on).
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ── Cache ────────────────────────────────────────────────────────────────

def _cache_key(api_key, city, units, lang):
    """Identify a query without keeping the API key in a dictionary key."""
    fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
    return (city.strip().casefold(), units, lang, fingerprint)


def _cached(key, max_age=None):
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        if max_age is not None and (time.time() - entry["timestamp"]) >= max_age:
            return None
        return entry["data"]


def _store(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "timestamp": time.time()}
        while len(_cache) > MAX_CACHE_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k]["timestamp"])
            del _cache[oldest]


def clear_cache():
    """Forget everything. Called when the weather settings change."""
    with _cache_lock:
        _cache.clear()


# ── HTTP ─────────────────────────────────────────────────────────────────

def _get_json(url, params):
    """GET a JSON document, turning every failure into a WeatherError."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WeatherError(_http_message(exc), status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise WeatherError(
            "Could not reach OpenWeatherMap. Check the network connection."
        ) from exc
    except (ValueError, TimeoutError, OSError) as exc:
        raise WeatherError(f"OpenWeatherMap sent an unreadable reply: {exc}") from exc


def _http_message(exc):
    if exc.code in (401, 403):
        return ("OpenWeatherMap rejected the API key. A new key can take an "
                "hour or two to become active.")
    if exc.code == 404:
        return "OpenWeatherMap does not recognise that place."
    if exc.code == 429:
        return "OpenWeatherMap rate limit reached. Try again in a few minutes."
    return f"OpenWeatherMap returned HTTP {exc.code}."


def _check_body(payload, city):
    """OpenWeatherMap also reports failures in a 200 body, via `cod`."""
    if not isinstance(payload, dict):
        return
    cod = payload.get("cod")
    if cod is None or str(cod) == "200":
        return
    message = payload.get("message") or f"error {cod}"
    if str(cod) == "404":
        raise WeatherError(f"No place called “{city}”.", status=404)
    raise WeatherError(f"OpenWeatherMap: {message}", status=_as_int(cod))


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Locating the place ───────────────────────────────────────────────────

def _coordinates(city):
    """Read "lat,lon" out of whatever the owner typed, or return None."""
    match = _COORD_RE.match(city)
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise WeatherError(f"“{city}” is not a valid latitude, longitude pair.",
                           status=400)
    return lat, lon


def _place_label(hit):
    """Name a geocoding hit the way the owner would recognise it."""
    name = hit.get("name") or ""
    country = hit.get("country") or ""
    state = hit.get("state") or ""
    if state and state != name:
        name = f"{name}, {state}"
    return f"{name}, {country}".strip(", ") if country else name


def _geocode(api_key, city):
    """Resolve a place name to coordinates.

    Returns (lat, lon, label), or None when geocoding could not be consulted at
    all — the caller then falls back to the weather endpoint's own `q=` lookup
    rather than reporting a failure that may not be one. An answer of "no such
    place" *is* a failure and is raised.
    """
    try:
        hits = _get_json(GEO_API, {"q": city, "limit": 1, "appid": api_key})
    except WeatherError as exc:
        if exc.status in (401, 403):
            raise                      # a bad key fails the same way on `q=`
        logger.warning(f"Geocoding unavailable ({exc}); falling back to name lookup")
        return None
    if not isinstance(hits, list):
        return None
    if not hits:
        raise WeatherError(
            f"No place matches “{city}”. Try “City, CC” "
            f"(Kaohsiung, TW) or a “latitude, longitude” pair.",
            status=404)
    top = hits[0]
    try:
        return float(top["lat"]), float(top["lon"]), _place_label(top)
    except (KeyError, TypeError, ValueError):
        return None


def _location_query(api_key, city):
    """The query parameters that identify the place, plus a display label."""
    coords = _coordinates(city)
    if coords:
        return {"lat": coords[0], "lon": coords[1]}, None

    located = _geocode(api_key, city)
    if located:
        lat, lon, label = located
        return {"lat": lat, "lon": lon}, label
    return {"q": city}, None


# ── Forecast shaping ─────────────────────────────────────────────────────

def _daily_forecast(raw, tz_offset, days=3):
    """Collapse the 3-hourly list into one entry per day.

    Timestamps are shifted into the *city's* timezone, not the device's — a
    frame in Taipei reading a forecast for Vancouver was splitting the days in
    the wrong place. Highs and lows are aggregated across each day's slots
    rather than taken from whichever slot happened to come first, which is what
    made the panel show a 4°C spread on a 12°C day.
    """
    buckets = {}
    order = []
    shift = timedelta(seconds=tz_offset or 0)

    for item in raw.get("list", []) or []:
        try:
            local = datetime.fromtimestamp(item["dt"], tz=timezone.utc) + shift
            main = item["main"]
            weather = (item.get("weather") or [{}])[0]
        except (KeyError, TypeError, ValueError, OSError):
            continue

        day_key = local.strftime("%Y-%m-%d")
        if day_key not in buckets:
            if len(buckets) >= days:
                continue
            buckets[day_key] = {
                "date": local.strftime("%m/%d"),
                "weekday": WEEKDAYS[local.weekday()],
                "temp_min": main.get("temp_min", main.get("temp")),
                "temp_max": main.get("temp_max", main.get("temp")),
                "description": weather.get("description", ""),
                "icon": weather.get("icon", ""),
                "_midday": abs(local.hour - 12),
            }
            order.append(day_key)
            continue

        bucket = buckets[day_key]
        low = main.get("temp_min", main.get("temp"))
        high = main.get("temp_max", main.get("temp"))
        if low is not None and (bucket["temp_min"] is None or low < bucket["temp_min"]):
            bucket["temp_min"] = low
        if high is not None and (bucket["temp_max"] is None or high > bucket["temp_max"]):
            bucket["temp_max"] = high
        # The icon should describe the daytime, not 03:00.
        distance = abs(local.hour - 12)
        if distance < bucket["_midday"]:
            bucket["_midday"] = distance
            bucket["description"] = weather.get("description", bucket["description"])
            bucket["icon"] = weather.get("icon", bucket["icon"])

    forecast = []
    for day_key in order:
        bucket = buckets[day_key]
        bucket.pop("_midday", None)
        bucket["temp_min"] = _round(bucket["temp_min"])
        bucket["temp_max"] = _round(bucket["temp_max"])
        forecast.append(bucket)
    return forecast


def _round(value):
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


# ── Public API ───────────────────────────────────────────────────────────

def fetch_weather_strict(api_key, city, units="metric", lang="en", force=False):
    """Current conditions plus a three-day forecast, or raise WeatherError.

    `force` skips the cache, which is what the Settings "Test" button needs:
    pressing it has to answer for the settings as they are now, not for
    whatever was asked an hour ago.
    """
    api_key = (api_key or "").strip()
    city = (city or "").strip()
    if not api_key:
        raise WeatherError("Add an OpenWeatherMap API key in Settings first.",
                           status=400)
    if not city:
        raise WeatherError("Set a city in Settings first.", status=400)

    units = units if units in UNITS else "metric"
    lang = (lang or "en").strip() or "en"
    key = _cache_key(api_key, city, units, lang)

    if not force:
        cached = _cached(key, CACHE_DURATION)
        if cached:
            return cached

    location, label = _location_query(api_key, city)
    params = {**location, "appid": api_key, "units": units, "lang": lang}

    current = _get_json(WEATHER_API, params)
    _check_body(current, city)
    try:
        main = current["main"]
        conditions = (current.get("weather") or [{}])[0]
    except (KeyError, TypeError) as exc:
        raise WeatherError("OpenWeatherMap sent a reading we could not read.") from exc

    forecast_raw = {}
    try:
        forecast_raw = _get_json(FORECAST_API, {**params, "cnt": 24})
        _check_body(forecast_raw, city)
    except WeatherError as exc:
        # The current reading is the useful half; a missing forecast should not
        # blank the panel.
        logger.warning(f"Forecast unavailable for {city!r}: {exc}")

    result = {
        # Prefer what OpenWeatherMap calls the place; fall back to the
        # geocoder's label and finally to whatever was typed, so the interface
        # always shows *which* place answered.
        "city": current.get("name") or label or city,
        "query": city,
        "temp": _round(main.get("temp")),
        "feels_like": _round(main.get("feels_like")),
        "temp_min": _round(main.get("temp_min")),
        "temp_max": _round(main.get("temp_max")),
        "humidity": main.get("humidity"),
        "description": conditions.get("description", ""),
        "icon": conditions.get("icon", ""),
        "wind_speed": (current.get("wind") or {}).get("speed", 0),
        "units": units,
        "forecast": _daily_forecast(forecast_raw, current.get("timezone", 0)),
        "updated": datetime.now().strftime("%H:%M"),
    }

    _store(key, result)
    logger.info(f"Weather updated: {result['city']} {result['temp']}° "
                f"(asked for {city!r})")
    return result


def fetch_weather(api_key, city, units="metric", lang="en", force=False):
    """Tolerant variant for the panel: a dict, or None. Never raises.

    On failure this falls back to the last good reading **for this same
    query**. Reusing another city's reading is what made a mistyped city look
    like a working one.
    """
    try:
        return fetch_weather_strict(api_key, city, units, lang, force=force)
    except WeatherError as exc:
        stale = _cached(_cache_key((api_key or "").strip(), city or "",
                                   units if units in UNITS else "metric",
                                   (lang or "en").strip() or "en"))
        if stale:
            logger.warning(f"Weather fetch failed ({exc}); showing the last "
                           f"reading for {city!r}")
            return stale
        logger.error(f"Weather fetch failed for {city!r}: {exc}")
        return None


def params_from_config(config):
    """The four settings that make up a weather query."""
    return {
        "api_key": config.get("weather_api_key", "") or "",
        "city": config.get("weather_city", "") or "",
        "units": config.get("weather_units", "metric") or "metric",
        "lang": config.get("weather_lang", "en") or "en",
    }


def is_configured(config):
    params = params_from_config(config)
    return bool(params["api_key"].strip() and params["city"].strip())


def fetch_for_config(config, force=False):
    """What the panel and the previews want: weather for the stored settings."""
    if not is_configured(config):
        return None
    return fetch_weather(force=force, **params_from_config(config))


# Weather icon to text mapping for e-paper
WEATHER_ICONS = {
    "01d": "☀", "01n": "\U0001F319",
    "02d": "⛅", "02n": "⛅",
    "03d": "☁", "03n": "☁",
    "04d": "☁", "04n": "☁",
    "09d": "\U0001F327", "09n": "\U0001F327",
    "10d": "\U0001F326", "10n": "\U0001F327",
    "11d": "⛈", "11n": "⛈",
    "13d": "❄", "13n": "❄",
    "50d": "\U0001F32B", "50n": "\U0001F32B",
}


def get_weather_symbol(icon_code):
    """Get a simple text symbol for weather condition."""
    return WEATHER_ICONS.get(icon_code, "?")
