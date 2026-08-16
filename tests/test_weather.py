#!/usr/bin/env python3
"""Tests for services/weather.py.

The bug these exist for: the cache was one module-level slot keyed on nothing
but time, so changing the city in Settings changed nothing for the next hour —
Test, the panel and the dashboard preview all kept reporting the previous
place. Every failure path made it worse by falling back to that same slot, so
a city OpenWeatherMap does not recognise looked exactly like a working one.

No network: urlopen is replaced with canned answers, so these run in CI and
fail for real reasons only.

    python3 -m pytest tests/
    python3 tests/test_weather.py     # also runs standalone
"""

import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO, "web") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "web"))

from services import weather  # noqa: E402


# ── A fake OpenWeatherMap ─────────────────────────────────────────────────

TAIPEI = {"lat": 25.03, "lon": 121.56, "name": "Taipei", "country": "TW"}
KAOHSIUNG = {"lat": 22.62, "lon": 120.31, "name": "Kaohsiung", "country": "TW"}
PLACES = {"taipei": TAIPEI, "kaohsiung": KAOHSIUNG}


class _Response(io.BytesIO):
    """Enough of an HTTPResponse for urlopen's context-manager use."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _current(place, temp):
    return {
        "cod": 200,
        "name": place["name"],
        "timezone": 28800,
        "main": {"temp": temp, "feels_like": temp + 1, "temp_min": temp - 2,
                 "temp_max": temp + 2, "humidity": 70},
        "weather": [{"description": "clear sky", "icon": "01d"}],
        "wind": {"speed": 3.1},
    }


def _forecast(temps):
    """A day of 3-hourly slots, so the daily high/low has something to fold."""
    slots = []
    # 2024-03-15, 00:00 UTC onwards. The city is UTC+8, so these land on the
    # 15th and 16th local — which is the point: the day boundary is the city's.
    base = 1710460800
    for index, temp in enumerate(temps):
        slots.append({
            "dt": base + index * 3 * 3600,
            "main": {"temp": temp, "temp_min": temp - 1, "temp_max": temp + 1},
            "weather": [{"description": f"desc {index}", "icon": f"0{index % 5 + 1}d"}],
        })
    return {"cod": "200", "list": slots}


class FakeUpstream:
    """Answers the three endpoints and counts what was asked."""

    def __init__(self, temps=None, geocode=None):
        self.calls = []
        self.temps = temps or {"Taipei": 21, "Kaohsiung": 29}
        self.geocode = geocode           # None = look the name up in PLACES

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else request
        self.calls.append(url)
        split = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(split.query))

        if "/geo/1.0/direct" in split.path:
            if self.geocode is not None:
                return _Response(json.dumps(self.geocode).encode())
            hit = PLACES.get(query.get("q", "").split(",")[0].strip().casefold())
            return _Response(json.dumps([hit] if hit else []).encode())

        place = self._place_for(query)
        if "/forecast" in split.path:
            return _Response(json.dumps(_forecast([18, 24, 15, 20])).encode())
        return _Response(json.dumps(
            _current(place, self.temps[place["name"]])).encode())

    def _place_for(self, query):
        if "lat" in query:
            for place in PLACES.values():
                if abs(float(query["lat"]) - place["lat"]) < 0.01:
                    return place
        name = (query.get("q") or "").split(",")[0].strip().casefold()
        return PLACES.get(name, TAIPEI)

    @property
    def weather_calls(self):
        return [u for u in self.calls if "/data/2.5/weather" in u]


class FakeFailure:
    """Every request fails the same way."""

    def __init__(self, code=500):
        self.code = code
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        if self.code is None:
            raise urllib.error.URLError("no route to host")
        raise urllib.error.HTTPError(
            getattr(request, "full_url", ""), self.code, "boom", None,
            io.BytesIO(b"{}"))


def _with_upstream(fake, fn):
    real = urllib.request.urlopen
    urllib.request.urlopen = fake
    weather.clear_cache()
    try:
        return fn()
    finally:
        urllib.request.urlopen = real
        weather.clear_cache()


# ── Tests ─────────────────────────────────────────────────────────────────

def test_changing_the_city_changes_the_answer():
    """The reported bug: Taipei kept coming back after the city was changed."""
    fake = FakeUpstream()

    def run():
        first = weather.fetch_weather_strict("KEY", "Taipei")
        second = weather.fetch_weather_strict("KEY", "Kaohsiung")
        return first, second

    first, second = _with_upstream(fake, run)
    assert first["city"] == "Taipei" and first["temp"] == 21
    assert second["city"] == "Kaohsiung" and second["temp"] == 29
    # …and it really asked, rather than being served the first entry.
    assert len(fake.weather_calls) == 2, fake.calls


def test_the_same_query_is_still_cached():
    """The fix must not turn every panel repaint into a pair of requests."""
    fake = FakeUpstream()

    def run():
        weather.fetch_weather_strict("KEY", "Taipei")
        weather.fetch_weather_strict("KEY", "Taipei")
        weather.fetch_weather_strict("KEY", "Taipei", force=True)

    _with_upstream(fake, run)
    assert len(fake.weather_calls) == 2, "second read should be cached, third forced"


def test_units_and_language_are_part_of_the_query():
    fake = FakeUpstream()

    def run():
        weather.fetch_weather_strict("KEY", "Taipei", units="metric")
        weather.fetch_weather_strict("KEY", "Taipei", units="imperial")
        weather.fetch_weather_strict("KEY", "Taipei", units="imperial", lang="zh_tw")

    _with_upstream(fake, run)
    assert len(fake.weather_calls) == 3, fake.calls


def test_an_unknown_city_is_reported_not_papered_over():
    """A place OWM does not know must not look like the previous one."""
    fake = FakeUpstream()

    def run():
        good = weather.fetch_weather_strict("KEY", "Taipei")
        try:
            weather.fetch_weather_strict("KEY", "Nowheresville")
            raise AssertionError("expected a WeatherError")
        except weather.WeatherError as exc:
            assert exc.status == 404, exc.status
        # The tolerant path must not hand back Taipei either.
        assert weather.fetch_weather("KEY", "Nowheresville") is None
        return good

    good = _with_upstream(fake, run)
    assert good["city"] == "Taipei"


def test_a_stale_reading_is_only_reused_for_its_own_query():
    """Offline, the panel may show the last reading — of the same place."""
    fake = FakeUpstream()
    _with_upstream(fake, lambda: None)     # just to reset

    real = urllib.request.urlopen
    weather.clear_cache()
    try:
        urllib.request.urlopen = fake
        weather.fetch_weather_strict("KEY", "Taipei")

        urllib.request.urlopen = FakeFailure(code=None)          # network down
        assert weather.fetch_weather("KEY", "Taipei")["city"] == "Taipei"
        assert weather.fetch_weather("KEY", "Kaohsiung") is None
    finally:
        urllib.request.urlopen = real
        weather.clear_cache()


def test_missing_settings_say_which_one():
    for api_key, city, expected in (("", "Taipei", "API key"), ("KEY", "", "city")):
        try:
            weather.fetch_weather_strict(api_key, city)
            raise AssertionError("expected a WeatherError")
        except weather.WeatherError as exc:
            assert expected in str(exc).lower() or expected in str(exc), exc


def test_a_rejected_key_is_named_as_such():
    fake = FakeFailure(code=401)
    real = urllib.request.urlopen
    weather.clear_cache()
    try:
        urllib.request.urlopen = fake
        weather.fetch_weather_strict("BAD-KEY", "Taipei")
        raise AssertionError("expected a WeatherError")
    except weather.WeatherError as exc:
        assert exc.status == 401
        assert "key" in str(exc).lower(), exc
    finally:
        urllib.request.urlopen = real
        weather.clear_cache()


def test_coordinates_are_accepted_and_skip_the_gazetteer():
    """The escape hatch for a place OWM does not know by name."""
    fake = FakeUpstream()

    def run():
        return weather.fetch_weather_strict("KEY", "22.62, 120.31")

    result = _with_upstream(fake, run)
    assert result["city"] == "Kaohsiung"
    assert not any("/geo/1.0/" in url for url in fake.calls), fake.calls

    for junk in ("999, 0", "0, 999"):
        try:
            weather.fetch_weather_strict("KEY", junk)
            raise AssertionError(f"{junk!r} should not parse as a place")
        except weather.WeatherError:
            pass


def test_the_daily_forecast_folds_the_whole_day():
    """High and low used to come from whichever slot happened to be first."""
    fake = FakeUpstream()
    result = _with_upstream(fake, lambda: weather.fetch_weather_strict("KEY", "Taipei"))

    assert result["forecast"], "no forecast built"
    first = result["forecast"][0]
    # Slots are 18/24/15/20 ±1 and all land on the same day in UTC+8.
    assert first["temp_max"] == 25, first
    assert first["temp_min"] == 14, first
    assert first["weekday"] in weather.WEEKDAYS


def test_a_missing_forecast_does_not_lose_the_reading():
    """Half an answer beats a blank panel."""
    fake = FakeUpstream()

    class ForecastDown(FakeUpstream):
        def __call__(self, request, timeout=None):
            url = getattr(request, "full_url", "")
            if "/forecast" in url:
                raise urllib.error.URLError("nope")
            return FakeUpstream.__call__(self, request, timeout)

    result = _with_upstream(ForecastDown(), lambda: weather.fetch_weather_strict("KEY", "Taipei"))
    assert result["temp"] == 21
    assert result["forecast"] == []
    assert fake.calls == []


def test_the_cache_cannot_grow_without_bound():
    fake = FakeUpstream(temps={"Taipei": 21, "Kaohsiung": 29})

    def run():
        for index in range(weather.MAX_CACHE_ENTRIES + 4):
            weather.fetch_weather_strict("KEY", f"{25.03 + index / 100:.2f}, 121.56")
        return len(weather._cache)

    assert _with_upstream(fake, run) <= weather.MAX_CACHE_ENTRIES


def test_config_helpers_read_the_stored_settings():
    from services.config import Config
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        config = Config(os.path.join(tmp, "config.json"))
        assert weather.is_configured(config) is False
        assert weather.fetch_for_config(config) is None      # no network touched

        config.update({"weather_api_key": "KEY", "weather_city": "Kaohsiung",
                       "weather_units": "imperial", "weather_lang": "zh_tw"})
        assert weather.is_configured(config) is True
        assert weather.params_from_config(config) == {
            "api_key": "KEY", "city": "Kaohsiung",
            "units": "imperial", "lang": "zh_tw"}

        result = _with_upstream(FakeUpstream(),
                                lambda: weather.fetch_for_config(config))
        assert result["city"] == "Kaohsiung"


# ── Standalone runner, so this works without pytest installed ─────────────

if __name__ == "__main__":
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
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
