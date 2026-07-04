"""
Weather service using OpenWeatherMap API.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger("vignette.weather")

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Cache weather data for ~1 hour (matches hourly e-paper refresh). Keyed by
# (city, units, lang) so changing any setting doesn't serve a stale result.
CACHE_DURATION = 3500  # seconds (~58 min)
_cache = {}
_cache_lock = threading.Lock()


def fetch_weather(api_key, city, units="metric", lang="en"):
    """Fetch current weather + 3-day forecast from OpenWeatherMap."""
    if not api_key or not city:
        return None

    cache_key = (city, units, lang)
    now = time.time()
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and (now - entry["timestamp"]) < CACHE_DURATION:
            return entry["data"]

    try:
        import urllib.request
        import urllib.parse

        # Current weather
        params = urllib.parse.urlencode({
            "q": city, "appid": api_key,
            "units": units, "lang": lang,
        })
        url = f"https://api.openweathermap.org/data/2.5/weather?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Vignette/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            current = json.loads(resp.read().decode())

        # Check API error response
        cod = current.get("cod")
        if cod and str(cod) != "200":
            logger.error(f"Weather API error: {current.get('message', cod)}")
            return entry["data"] if entry else None

        # 5-day forecast (3-hour intervals)
        url_fc = f"https://api.openweathermap.org/data/2.5/forecast?{params}&cnt=24"
        req_fc = urllib.request.Request(url_fc, headers={"User-Agent": "Vignette/1.0"})
        with urllib.request.urlopen(req_fc, timeout=10) as resp:
            forecast_raw = json.loads(resp.read().decode())

        # Bucket into per-day forecasts in the CITY's timezone, aggregating the
        # true daily high/low across every 3-hour slot (not just the first,
        # pre-dawn one) and picking the slot nearest local noon for the icon.
        city_offset = forecast_raw.get("city", {}).get("timezone", 0)  # seconds
        daily = {}
        order = []
        for item in forecast_raw.get("list", []):
            local = datetime.utcfromtimestamp(item["dt"] + city_offset)
            day_key = local.strftime("%m/%d")
            tmin = item["main"]["temp_min"]
            tmax = item["main"]["temp_max"]
            noon_delta = abs(local.hour - 12)
            if day_key not in daily:
                if len(daily) >= 3:
                    continue  # already have 3 distinct days
                daily[day_key] = {
                    "date": day_key,
                    "weekday": _WEEKDAYS[local.weekday()],
                    "temp_min": tmin,
                    "temp_max": tmax,
                    "description": item["weather"][0]["description"],
                    "icon": item["weather"][0]["icon"],
                    "_noon_delta": noon_delta,
                }
                order.append(day_key)
            else:
                d = daily[day_key]
                d["temp_min"] = min(d["temp_min"], tmin)
                d["temp_max"] = max(d["temp_max"], tmax)
                if noon_delta < d["_noon_delta"]:
                    d["_noon_delta"] = noon_delta
                    d["description"] = item["weather"][0]["description"]
                    d["icon"] = item["weather"][0]["icon"]

        forecast_list = []
        for k in order:
            d = daily[k]
            d.pop("_noon_delta", None)
            d["temp_min"] = round(d["temp_min"])
            d["temp_max"] = round(d["temp_max"])
            forecast_list.append(d)

        result = {
            "city": current.get("name", city),
            "temp": round(current["main"]["temp"]),
            "feels_like": round(current["main"]["feels_like"]),
            "temp_min": round(current["main"]["temp_min"]),
            "temp_max": round(current["main"]["temp_max"]),
            "humidity": current["main"]["humidity"],
            "description": current["weather"][0]["description"],
            "icon": current["weather"][0]["icon"],
            "wind_speed": current.get("wind", {}).get("speed", 0),
            "forecast": forecast_list,
            "updated": datetime.now().strftime("%H:%M"),
        }

        with _cache_lock:
            _cache[cache_key] = {"data": result, "timestamp": now}
        logger.info(f"Weather updated: {result['city']} {result['temp']}°")
        return result

    except Exception as e:
        logger.error(f"Weather fetch failed: {e}", exc_info=True)
        return entry["data"] if entry else None  # stale cache on error


# Weather icon to text mapping for e-paper
WEATHER_ICONS = {
    "01d": "☀", "01n": "🌙",
    "02d": "⛅", "02n": "⛅",
    "03d": "☁", "03n": "☁",
    "04d": "☁", "04n": "☁",
    "09d": "🌧", "09n": "🌧",
    "10d": "🌦", "10n": "🌧",
    "11d": "⛈", "11n": "⛈",
    "13d": "❄", "13n": "❄",
    "50d": "🌫", "50n": "🌫",
}


def get_weather_symbol(icon_code):
    """Get a simple text symbol for weather condition."""
    return WEATHER_ICONS.get(icon_code, "?")
