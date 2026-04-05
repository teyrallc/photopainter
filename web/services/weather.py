"""
Weather service using OpenWeatherMap API.
"""

import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger("vignette.weather")

# Cache weather data for 15 minutes
CACHE_DURATION = 50  # seconds - allow 1-minute refresh cycle to get fresh data
_cache = {"data": None, "timestamp": 0}


def fetch_weather(api_key, city, units="metric", lang="en"):
    """Fetch current weather + 3-day forecast from OpenWeatherMap."""
    if not api_key or not city:
        return None

    now = time.time()
    if _cache["data"] and (now - _cache["timestamp"]) < CACHE_DURATION:
        return _cache["data"]

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
            return _cache.get("data")

        # 5-day forecast (3-hour intervals)
        url_fc = f"https://api.openweathermap.org/data/2.5/forecast?{params}&cnt=24"
        req_fc = urllib.request.Request(url_fc, headers={"User-Agent": "Vignette/1.0"})
        with urllib.request.urlopen(req_fc, timeout=10) as resp:
            forecast_raw = json.loads(resp.read().decode())

        # Extract daily forecasts (one per day)
        daily = {}
        for item in forecast_raw.get("list", []):
            dt = datetime.fromtimestamp(item["dt"])
            day_key = dt.strftime("%m/%d")
            if day_key not in daily and len(daily) < 3:
                daily[day_key] = {
                    "date": day_key,
                    "weekday": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()],
                    "temp_min": round(item["main"]["temp_min"]),
                    "temp_max": round(item["main"]["temp_max"]),
                    "description": item["weather"][0]["description"],
                    "icon": item["weather"][0]["icon"],
                }

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
            "forecast": list(daily.values()),
            "updated": datetime.now().strftime("%H:%M"),
        }

        _cache["data"] = result
        _cache["timestamp"] = now
        logger.info(f"Weather updated: {result['city']} {result['temp']}°")
        return result

    except Exception as e:
        logger.error(f"Weather fetch failed: {e}", exc_info=True)
        return _cache.get("data")  # Return stale cache on error


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
