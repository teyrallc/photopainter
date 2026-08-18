#!/usr/bin/env python3
"""Smoke tests for the Vignette web service.

The e-paper panel and NetworkManager are stubbed out, so this runs anywhere —
no Raspberry Pi, no display, no WiFi. That is the point: the bug where the
Settings page's "Factory Reset" and "Show QR" buttons both raised a TypeError
would have been caught by nothing more than asking every route for a response.

    python3 -m pytest tests/
    python3 tests/test_smoke.py     # also runs standalone, without pytest
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Hardware and system stubs ─────────────────────────────────────────────

class _EPD:
    def init(self): pass
    def Clear(self): pass
    def sleep(self): pass
    def getbuffer(self, image): return b""
    def display(self, buffer): pass


def _install_stubs():
    """Replace the Pi-only pieces so the app can be imported off-device."""
    epd_pkg = types.ModuleType("waveshare_epd")
    sys.modules.setdefault("waveshare_epd", epd_pkg)

    # Both panel drivers, so the model-selection path is exercised rather than
    # only the one that happens to be the default.
    for name in ("epd7in3e", "epd7in3f"):
        mod = types.ModuleType(f"waveshare_epd.{name}")
        mod.EPD = _EPD
        setattr(epd_pkg, name, mod)
        sys.modules.setdefault(f"waveshare_epd.{name}", mod)

    import subprocess
    subprocess.run = lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout="", stderr="")
    subprocess.check_output = lambda cmd, *a, **k: (
        "192.168.1.50\n" if "-I" in cmd else "vignette\n")
    subprocess.Popen = lambda *a, **k: types.SimpleNamespace(pid=1)


def _load_app():
    _install_stubs()
    sys.path.insert(0, os.path.join(REPO, "web"))
    sys.path.insert(0, os.path.join(REPO, "lib"))

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "vignette_app", os.path.join(REPO, "web", "app.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["vignette_app"] = module
    spec.loader.exec_module(module)
    return module


# _install_stubs() replaces subprocess.run and Popen process-wide so the app
# never shells out to nmcli during a test. Keep handles on the real ones: a
# test that needs to execute something would otherwise assert against the
# stub's canned success and pass for the wrong reason. Both are needed —
# subprocess.run() calls Popen() internally, so restoring only run() fails.
_real_run = subprocess.run
_real_popen = subprocess.Popen


def run_real(command, **kwargs):
    """Actually execute `command`, stepping around the app-wide stubs."""
    stubbed_run, stubbed_popen = subprocess.run, subprocess.Popen
    subprocess.run, subprocess.Popen = _real_run, _real_popen
    try:
        return _real_run(command, **kwargs)
    finally:
        subprocess.run, subprocess.Popen = stubbed_run, stubbed_popen


vapp = _load_app()
app = vapp.app
app.config["TESTING"] = True
config = vapp.config

# The test client sends Host: localhost, so a same-origin header must match it.
SAME_ORIGIN = {"Origin": "http://localhost"}

# Endpoints that reboot the box, rewrite the network, or take minutes.
DESTRUCTIVE = {
    "/api/system/update",
    "/api/system/reboot",
    "/api/system/shutdown",
    "/api/reset",
    "/api/wifi/connect",
}

# Routes that reach the public internet. "The upstream is unreachable" is the
# normal answer in CI and is reported as 502, which is not a crash — a crash is
# still a 500 and still fails the sweep below.
UPSTREAM = {"/api/weather"}


def _paired_client():
    """A signed-in session on a device that has completed setup."""
    from werkzeug.security import generate_password_hash
    config.update({
        "setup_complete": True,
        "admin_email": "test@example.com",
        "admin_password_hash": generate_password_hash("test-password"),
    })
    client = app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True
        session["email"] = "test@example.com"
    return client


# ── Tests ─────────────────────────────────────────────────────────────────

def test_every_get_route_responds():
    """No route may raise. This is the check that was missing."""
    client = _paired_client()
    failures = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments:
            continue
        response = client.get(rule.rule)
        if response.status_code >= 500 and not (
                rule.rule in UPSTREAM and response.status_code == 502):
            failures.append((rule.rule, response.status_code,
                             response.get_data(as_text=True)[:200]))
    assert not failures, f"routes returned 5xx: {failures}"


def test_every_post_route_responds():
    client = _paired_client()
    failures = []
    for rule in app.url_map.iter_rules():
        if "POST" not in rule.methods or rule.arguments:
            continue
        if rule.rule in DESTRUCTIVE:
            continue
        response = client.post(rule.rule, json={}, headers=SAME_ORIGIN)
        if response.status_code >= 500:
            failures.append((rule.rule, response.status_code,
                             response.get_data(as_text=True)[:200]))
    assert not failures, f"routes returned 5xx: {failures}"


def test_qr_setup_renders_without_an_ip():
    """Two of the three callers have no IP to pass; none may crash."""
    from services import display_mgr
    display_mgr.display_qr_setup()
    display_mgr.display_qr_setup("192.168.1.50")


def test_secrets_never_leave_the_device():
    from services.config import SECRET_KEYS

    # Sentinels are deliberately unlike any markup on the page: an earlier
    # version of this test used "gdrive-secret", which is a substring of the
    # form's own id="s-gdrive-secret" and reported a leak that was not there.
    sentinels = {
        "wifi_password": "ZZWIFIPWSENTINEL01",
        "gdrive_client_secret": "ZZGDRIVESENTINEL02",
        "weather_api_key": "ZZWEATHERSENTINEL03",
    }
    client = _paired_client()
    config.update(sentinels)

    payload = client.get("/api/config").get_json()
    exposed = [k for k in SECRET_KEYS if k in payload]
    assert not exposed, f"/api/config exposed {exposed}"
    assert payload["weather_api_key_configured"] is True   # still knowable

    status = client.get("/api/status").get_json()["config"]
    exposed = [k for k in SECRET_KEYS if k in status]
    assert not exposed, f"/api/status exposed {exposed}"

    # And no stored secret may appear anywhere in a rendered page.
    for page in ("/settings", "/setup", "/", "/gallery"):
        html = client.get(page).get_data(as_text=True)
        found = [k for k, v in sentinels.items() if v in html]
        assert not found, f"{page} rendered {found}"


def test_config_post_cannot_touch_the_admin_credential():
    client = _paired_client()
    before = config.get("admin_password_hash")
    client.post("/api/config", headers=SAME_ORIGIN, json={
        "admin_password_hash": "attacker-controlled",
        "admin_email": "attacker@example.com",
        "setup_complete": False,
        "weather_city": "Taipei",
    })
    assert config.get("admin_password_hash") == before
    assert config.get("admin_email") == "test@example.com"
    assert config.get("setup_complete") is True
    assert config.get("weather_city") == "Taipei"     # ordinary keys still apply


def test_cross_origin_state_change_is_blocked():
    client = _paired_client()
    blocked = client.post("/api/page/switch",
                          headers={"Origin": "https://evil.example"},
                          json={"page": "home"})
    assert blocked.status_code == 403
    allowed = client.post("/api/page/switch", headers=SAME_ORIGIN, json={"page": "home"})
    assert allowed.status_code == 200


def test_wifi_endpoints_are_closed_once_paired():
    config.set("setup_complete", True)
    anonymous = app.test_client()
    assert anonymous.get("/api/wifi/scan").status_code == 401
    # The post-join poll has to answer before anyone can sign in.
    assert anonymous.get("/api/wifi/connect/status").status_code == 200

    config.set("setup_complete", False)
    assert app.test_client().get("/api/wifi/scan").status_code == 200
    assert app.test_client().get("/").status_code == 200
    # …but the rest of the API is not open just because we are unpaired.
    assert app.test_client().get("/api/config").status_code == 401
    config.set("setup_complete", True)


def test_display_cannot_escape_the_output_directory():
    client = _paired_client()
    with tempfile.TemporaryDirectory() as tmp:
        from PIL import Image
        outside = os.path.join(tmp, "outside.png")
        Image.new("RGB", (8, 8)).save(outside)
        escape = os.path.relpath(outside, vapp.OUTPUT_DIR)
        response = client.post("/api/display", json={"filename": escape},
                               headers=SAME_ORIGIN)
    assert response.status_code == 404


def test_bad_upload_is_rejected_and_leaves_nothing_behind():
    client = _paired_client()
    before = set(os.listdir(vapp.OUTPUT_DIR))
    response = client.post(
        "/api/upload", headers=SAME_ORIGIN, content_type="multipart/form-data",
        data={"file": (io.BytesIO(b"definitely not a png"), "fake.png")})
    after = set(os.listdir(vapp.OUTPUT_DIR))

    assert response.status_code == 400
    assert after == before, f"left behind: {after - before}"


def test_good_upload_round_trips():
    from PIL import Image
    client = _paired_client()
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buffer, "PNG")
    buffer.seek(0)

    response = client.post(
        "/api/upload", headers=SAME_ORIGIN, content_type="multipart/form-data",
        data={"file": (buffer, "smoke-test.png")})
    assert response.status_code == 200

    name = response.get_json()["filename"]
    try:
        assert client.get(f"/api/preview/{name}").status_code == 200
        assert client.post("/api/display", json={"filename": name},
                           headers=SAME_ORIGIN).status_code == 200
    finally:
        client.delete(f"/api/images/{name}", headers=SAME_ORIGIN)
    assert not os.path.exists(os.path.join(vapp.OUTPUT_DIR, name))


def test_otp_burns_after_repeated_wrong_guesses():
    from services import auth_mgr
    auth_mgr.generate_otp("test@example.com")
    for _ in range(auth_mgr.MAX_OTP_ATTEMPTS + 1):
        assert auth_mgr.verify_otp("test@example.com", "000000") is False
    assert "test@example.com" not in auth_mgr._otp_cache


def test_pairing_hotspot_credentials_are_per_device():
    """No two units may ship with the same joinable network."""
    from services import device_id
    from services.config import Config

    assert device_id.ap_ssid().startswith("Vignette-")
    assert device_id.ap_ssid() == device_id.ap_ssid()          # stable

    with tempfile.TemporaryDirectory() as tmp:
        fresh = Config(os.path.join(tmp, "config.json"))
        ssid, password = device_id.ap_credentials(fresh)
        assert len(password) >= 8                              # WPA2 minimum
        assert password != "vignette123"
        # Minted once, then kept.
        assert device_id.ap_credentials(fresh)[1] == password
        assert fresh.get("ap_password") == password

    assert device_id.is_own_ap(device_id.ap_ssid())
    assert device_id.is_own_ap("Vignette-Setup")               # legacy name
    assert not device_id.is_own_ap("SomeHomeNetwork")


def test_pairing_password_is_never_disclosed():
    client = _paired_client()
    _, password = __import__("services.device_id", fromlist=["x"]).ap_credentials(config)

    assert "ap_password" not in client.get("/api/config").get_json()
    for page in ("/settings", "/", "/wifi"):
        assert password not in client.get(page).get_data(as_text=True)


def test_panel_model_is_selectable():
    from services import epd
    assert isinstance(epd.get_epd("7in3e"), _EPD)
    assert isinstance(epd.get_epd("7in3f"), _EPD)
    # An unknown value must fall back rather than raise on a device in a field.
    assert isinstance(epd.get_epd("nonsense"), _EPD)
    assert epd.normalize_model(None) == epd.DEFAULT_MODEL


def test_remote_access_reports_state_without_leaking_the_token():
    config.update({"ngrok_authtoken": "ZZNGROKSENTINEL04",
                   "remote_access_enabled": True})
    client = _paired_client()

    payload = client.get("/api/remote").get_json()
    assert payload["configured"] is True
    assert "ZZNGROKSENTINEL04" not in json.dumps(payload)
    assert payload["local_url"].startswith("http://")

    assert "ngrok_authtoken" not in client.get("/api/config").get_json()
    assert "ZZNGROKSENTINEL04" not in client.get("/settings").get_data(as_text=True)


def test_tunnel_host_is_accepted_as_same_origin():
    """A request arriving over the tunnel must not read as cross-site."""
    from services.remote_access import service as remote
    client = _paired_client()

    with remote._lock:
        remote._url = "https://abcd-1-2-3-4.ngrok-free.app"
    try:
        ok = client.post("/api/page/switch",
                         headers={"Origin": "https://abcd-1-2-3-4.ngrok-free.app"},
                         json={"page": "home"})
        assert ok.status_code == 200, ok.get_data(as_text=True)

        blocked = client.post("/api/page/switch",
                              headers={"Origin": "https://evil.example"},
                              json={"page": "home"})
        assert blocked.status_code == 403
    finally:
        with remote._lock:
            remote._url = None


def test_session_cookie_is_secure_only_over_https():
    from werkzeug.security import generate_password_hash
    config.update({"admin_email": "test@example.com",
                   "admin_password_hash": generate_password_hash("test-password"),
                   "setup_complete": True})

    def cookie_for(scheme):
        c = app.test_client()
        r = c.post("/auth/login", base_url=f"{scheme}://localhost",
                   headers={"Origin": f"{scheme}://localhost"},
                   json={"email": "test@example.com", "password": "test-password"})
        return next((s for s in r.headers.getlist("Set-Cookie")
                     if s.startswith("session=")), "")

    plain, secure = cookie_for("http"), cookie_for("https")
    assert "Secure" not in plain, plain          # LAN sign-in must keep working
    assert "Secure" in secure, secure            # tunnelled sign-in is marked
    assert "SameSite=Lax" in plain and "HttpOnly" in plain


def test_factory_reset_can_take_the_photos_with_it():
    from PIL import Image
    client = _paired_client()
    victim = os.path.join(vapp.OUTPUT_DIR, "previous-owner.png")
    Image.new("RGB", (8, 8)).save(victim)
    try:
        r = client.post("/api/reset", headers=SAME_ORIGIN,
                        json={"delete_photos": True})
        assert r.status_code == 200
        assert r.get_json()["photos_deleted"] >= 1
        assert not os.path.exists(victim)
    finally:
        if os.path.exists(victim):
            os.remove(victim)
        config.set("setup_complete", True)


def test_watchdog_falls_back_without_discarding_credentials():
    """One minute offline reaches the pairing screen — but keeps the secrets."""
    from services.net_watchdog import NetWatchdog

    config.update({"wifi_ssid": "HomeNet", "wifi_password": "keep-me",
                   "admin_email": "test@example.com", "setup_complete": True})

    calls = []
    watchdog = NetWatchdog()
    watchdog.init(
        config,
        active_ssid=lambda: None,                       # link is down
        start_ap=lambda: calls.append("start_ap"),
        stop_ap=lambda: calls.append("stop_ap"),
        scan_wifi=lambda: calls.append("scan"),
        show_pairing_screen=lambda: calls.append("pairing_screen"),
        connect_saved=lambda: False,
        is_pairing_in_progress=lambda: False,
    )
    watchdog._enter_fallback()

    assert "start_ap" in calls and "pairing_screen" in calls
    assert config.get("setup_complete") is False        # portal is reachable
    assert config.get("wifi_ssid") == "HomeNet"         # NOT a reset
    assert config.get("wifi_password") == "keep-me"
    assert config.get("admin_email") == "test@example.com"

    # And it stands down again once a network comes back.
    watchdog._leave_fallback("HomeNet")
    assert config.get("setup_complete") is True
    assert watchdog.fallback_active is False


def test_watchdog_ignores_our_own_hotspot():
    """Seeing our own pairing SSID must not read as 'the network is fine'."""
    from services.net_watchdog import NetWatchdog
    from services import device_id

    watchdog = NetWatchdog()
    watchdog.init(config, active_ssid=lambda: device_id.ap_ssid(),
                  start_ap=lambda: None, stop_ap=lambda: None,
                  scan_wifi=lambda: None, show_pairing_screen=lambda: None,
                  connect_saved=lambda: False, is_pairing_in_progress=lambda: False)
    assert watchdog._joined_ssid() is None

    watchdog.init(config, active_ssid=lambda: "HomeNet",
                  start_ap=lambda: None, stop_ap=lambda: None,
                  scan_wifi=lambda: None, show_pairing_screen=lambda: None,
                  connect_saved=lambda: False, is_pairing_in_progress=lambda: False)
    assert watchdog._joined_ssid() == "HomeNet"


def test_no_page_names_the_old_shared_hotspot():
    """Every unit used to advertise Vignette-Setup / vignette123."""
    from services import device_id
    client = _paired_client()
    ssid = device_id.ap_ssid()

    for page in ("/wifi", "/settings"):
        html = client.get(page).get_data(as_text=True)
        assert "vignette123" not in html, page
        assert "Vignette-Setup" not in html, page

    # …and the page that talks about pairing names *this* device's hotspot.
    assert ssid in client.get("/wifi").get_data(as_text=True)


def test_legacy_secrets_left_by_an_older_build_are_still_redacted():
    """A device updating in the field keeps keys this version dropped.

    public_dict() filtered on SECRET_KEYS alone, so `smtp_password` — retired
    from the defaults but still sitting in every existing config.json — went
    straight out to the browser.
    """
    from services.config import Config, is_secret_name
    import json as _json

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as f:
            _json.dump({
                "weather_city": "Taipei",
                "smtp_password": "ZZLEGACYSMTP05",          # retired key
                "some_future_api_token": "ZZFUTURE06",      # not declared yet
                "wifi_password": "ZZWIFI07",                # declared secret
            }, f)

        payload = _json.dumps(Config(path).public_dict())

    for sentinel in ("ZZLEGACYSMTP05", "ZZFUTURE06", "ZZWIFI07"):
        assert sentinel not in payload, sentinel
    assert "Taipei" in payload            # ordinary settings still come through

    assert is_secret_name("smtp_password")
    assert is_secret_name("some_future_api_token")
    assert not is_secret_name("weather_city")
    # The generated markers are booleans, not values, and must survive.
    assert not is_secret_name("weather_api_key_configured")


def test_remote_access_falls_back_to_the_prototype_token():
    """A field update has no token stored and must not go LAN-only."""
    from services.remote_access import RemoteAccess
    from services.config import Config
    import json as _json

    with tempfile.TemporaryDirectory() as tmp:
        fresh = Config(os.path.join(tmp, "config.json"))
        service = RemoteAccess()
        service.init(fresh)

        state = service.state()
        assert state["configured"] is True
        assert state["shared_token"] is True, "should report the shared account"

        fresh.set("ngrok_authtoken", "ZZOWNTOKEN08")
        assert service._authtoken() == "ZZOWNTOKEN08"      # own token wins
        assert service.state()["shared_token"] is False
        # And it is still never transmitted.
        assert "ZZOWNTOKEN08" not in _json.dumps(service.state())
        assert "ZZOWNTOKEN08" not in _json.dumps(fresh.public_dict())


def test_cloudflare_provider_uses_the_fixed_address():
    """A named tunnel's hostname never changes — that is the point of it."""
    from services.remote_access import RemoteAccess, normalize_url
    from services.config import Config

    # Whatever the owner types has to resolve to one canonical origin.
    assert normalize_url("yilin.example.com") == "https://yilin.example.com"
    assert normalize_url("https://yilin.example.com/") == "https://yilin.example.com"
    assert normalize_url("  http://yilin.example.com  ") == "http://yilin.example.com"
    assert normalize_url("") == ""
    assert normalize_url(None) == ""

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(os.path.join(tmp, "config.json"))
        cfg.update({"remote_access_provider": "cloudflare",
                    "remote_public_url": "yilin.example.com"})

        service = RemoteAccess()
        service.init(cfg)
        assert service.provider == "cloudflare"
        assert service.configured_host == "yilin.example.com"
        assert service.state()["configured"] is True
        assert service.state()["stable_url"] is True
        # The ngrok fallback must not apply to a Cloudflare device.
        assert service.using_prototype_token() is False

        # No address configured is a reportable problem, not a silent no-op.
        cfg.set("remote_public_url", "")
        assert service.state()["configured"] is False


def test_provider_none_disables_remote_access():
    from services.remote_access import RemoteAccess
    from services.config import Config

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(os.path.join(tmp, "config.json"))
        cfg.set("remote_access_provider", "none")
        service = RemoteAccess()
        service.init(cfg)
        assert service.provider == "none"
        assert service.state()["configured"] is False
        assert service.using_prototype_token() is False

        # An unrecognised value must fall back, not crash a device in a field.
        cfg.set("remote_access_provider", "nonsense")
        assert service.provider == "ngrok"


def test_configured_domain_counts_as_same_origin():
    """Requests through the tunnel must survive a reconnect window.

    The host check consulted only the *live* tunnel URL, so while the tunnel
    was down every request arriving through it read as cross-site and was
    rejected with 403 — exactly when the owner is trying to see what is wrong.
    """
    config.update({"remote_access_provider": "cloudflare",
                   "remote_public_url": "https://yilin.example.com",
                   "setup_complete": True})
    client = _paired_client()

    from services.remote_access import service as remote
    with remote._lock:
        remote._url = None              # tunnel currently reports itself down

    try:
        ok = client.post("/api/page/switch",
                         headers={"Origin": "https://yilin.example.com"},
                         json={"page": "home"})
        assert ok.status_code == 200, ok.get_data(as_text=True)

        blocked = client.post("/api/page/switch",
                              headers={"Origin": "https://evil.example"},
                              json={"page": "home"})
        assert blocked.status_code == 403
    finally:
        config.update({"remote_access_provider": "ngrok",
                       "remote_public_url": ""})


def test_tunnel_setup_script_is_sane():
    script = os.path.join(REPO, "scripts", "setup-tunnel.sh")
    assert os.access(script, os.X_OK), "setup-tunnel.sh is not executable"

    body = open(script).read()
    # It must configure the app, not just the tunnel, or the panel keeps
    # showing the old ngrok address.
    assert "remote_access_provider" in body and "remote_public_url" in body
    # And it must write config.json atomically — it holds the WiFi password.
    assert "os.replace" in body

    result = run_real(["bash", script, "--help"],
                      capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "set -euo" not in result.stdout, "help is leaking script body"

    # A bad hostname must be refused before anything is installed.
    bad = run_real(["bash", script, "not a hostname"],
                   capture_output=True, text=True, timeout=30)
    assert bad.returncode != 0
    assert "does not look like a hostname" in bad.stdout + bad.stderr


@contextlib.contextmanager
def fake_upstream(handler):
    """Answer every outbound HTTP call from `handler` for the block's duration.

    Nothing in tests/ may reach the internet: these features are the two that
    talk to one, and a test that quietly depends on OpenWeatherMap or iCloud
    being up is worse than no test.
    """
    import urllib.request
    real = urllib.request.urlopen
    urllib.request.urlopen = handler
    try:
        yield
    finally:
        urllib.request.urlopen = real


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_changing_the_city_changes_what_the_test_button_reports():
    """The reported bug, end to end: save Kaohsiung, press Test, see Taipei.

    The city is saved through /api/config and read back through the same
    endpoint Settings' Test button calls, so this covers the wiring as well as
    the cache: nothing about the first answer may survive into the second.
    """
    from services import weather

    def upstream(request, timeout=None):
        url = request.full_url
        if "/geo/1.0/direct" in url:
            name = "Kaohsiung" if "Kaohsiung" in url else "Taipei"
            lat = "22.62" if name == "Kaohsiung" else "25.03"
            return _Body(json.dumps([{"name": name, "country": "TW",
                                      "lat": lat, "lon": "120.31"}]).encode())
        name = "Kaohsiung" if "22.62" in url else "Taipei"
        if "/forecast" in url:
            return _Body(json.dumps({"cod": "200", "list": []}).encode())
        return _Body(json.dumps({
            "cod": 200, "name": name, "timezone": 28800,
            "main": {"temp": 29 if name == "Kaohsiung" else 21, "feels_like": 30,
                     "temp_min": 20, "temp_max": 31, "humidity": 70},
            "weather": [{"description": "clear sky", "icon": "01d"}],
        }).encode())

    client = _paired_client()
    config.set("weather_api_key", "TEST-KEY")
    weather.clear_cache()

    try:
        with fake_upstream(upstream):
            client.post("/api/config", headers=SAME_ORIGIN,
                        json={"weather_city": "Taipei"})
            first = client.get("/api/weather?refresh=1").get_json()

            client.post("/api/config", headers=SAME_ORIGIN,
                        json={"weather_city": "Kaohsiung"})
            second = client.get("/api/weather?refresh=1").get_json()
            # …and the panel's own (cached) path must see it too.
            third = client.get("/api/weather").get_json()
    finally:
        weather.clear_cache()
        config.update({"weather_api_key": "", "weather_city": ""})

    assert first["city"] == "Taipei", first
    assert second["city"] == "Kaohsiung", second
    assert third["city"] == "Kaohsiung", third


def test_a_weather_failure_is_reported_rather_than_answered_with_stale_data():
    from services import weather
    import urllib.error

    def upstream(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "nope", None,
                                     io.BytesIO(b"{}"))

    client = _paired_client()
    config.update({"weather_api_key": "BAD-KEY", "weather_city": "Taipei"})
    weather.clear_cache()

    try:
        with fake_upstream(upstream):
            response = client.get("/api/weather?refresh=1")
    finally:
        weather.clear_cache()
        config.update({"weather_api_key": "", "weather_city": ""})

    assert response.status_code == 400, response.get_data(as_text=True)
    assert "key" in response.get_json()["error"].lower()


# ── iCloud shared album ───────────────────────────────────────────────────

ICLOUD_TOKEN = "B0abcdefghijkl"


def _icloud_upstream(photo_guids=("PHOTO-1", "PHOTO-2")):
    """Serve a two-photo album plus real PNG bytes for the assets."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (10, 120, 200)).save(buffer, "PNG")
    png = buffer.getvalue()

    def upstream(request, timeout=None):
        url = request.full_url
        if url.endswith("/webstream"):
            return _Body(json.dumps({
                "streamName": "Family",
                "photos": [{
                    "photoGuid": guid,
                    "caption": guid,
                    "dateCreated": "2024-03-15T09:00:00Z",
                    "derivatives": {"2048": {"checksum": f"{guid}-c",
                                             "width": "2048", "height": "1365",
                                             "fileSize": str(len(png))}},
                } for guid in photo_guids],
            }).encode())
        if url.endswith("/webasseturls"):
            wanted = json.loads(request.data.decode())["photoGuids"]
            return _Body(json.dumps({"items": {
                f"{guid}-c": {"url_location": "cvws.icloud-content.com",
                              "url_path": f"/S/{guid}.jpg"}
                for guid in wanted}}).encode())
        return _Body(png)

    return upstream


def _forget_icloud():
    vapp.icloud.forget_album()
    vapp.icloud_ledger.clear()
    config.update({"icloud_album_url": "", "icloud_album_token": "",
                   "icloud_album_name": "", "icloud_connected": False})


def test_icloud_endpoints_need_an_album_first():
    client = _paired_client()
    _forget_icloud()
    for path in ("/api/icloud/photos",):
        assert client.get(path).status_code == 401, path
    for path in ("/api/icloud/import", "/api/icloud/sync"):
        assert client.post(path, headers=SAME_ORIGIN, json={}).status_code == 401, path

    # A link that is not an album link must be refused before anything is
    # stored — and refused as the caller's mistake, not as a server fault.
    for junk in ("", "https://example.com/not-an-album", "../../etc/passwd"):
        bad = client.post("/api/icloud/connect", headers=SAME_ORIGIN,
                          json={"url": junk})
        assert bad.status_code == 400, (junk, bad.status_code)
        assert config.get("icloud_connected") is False
        assert config.get("icloud_album_token") == ""


def test_the_album_link_cannot_be_set_behind_the_validation():
    """/api/config must not be able to claim a connection nothing has reached."""
    client = _paired_client()
    _forget_icloud()
    client.post("/api/config", headers=SAME_ORIGIN, json={
        "icloud_album_url": "https://www.icloud.com/sharedalbum/#B0deadbeef99",
        "icloud_album_token": "B0deadbeef99",
        "icloud_connected": True,
    })
    assert config.get("icloud_album_url") == ""
    assert config.get("icloud_connected") is False
    # The schedule, which needs no validation, still applies.
    client.post("/api/config", headers=SAME_ORIGIN,
                json={"icloud_sync_interval": 21600, "icloud_auto_sync": False})
    assert config.get("icloud_sync_interval") == 21600
    config.set("icloud_auto_sync", True)


def test_an_album_connects_imports_once_and_syncs_without_duplicating():
    client = _paired_client()
    _forget_icloud()
    before = set(os.listdir(vapp.OUTPUT_DIR))

    try:
        with fake_upstream(_icloud_upstream()):
            connected = client.post(
                "/api/icloud/connect", headers=SAME_ORIGIN,
                json={"url": f"https://www.icloud.com/sharedalbum/#{ICLOUD_TOKEN}"})
            imported = client.post("/api/icloud/import", headers=SAME_ORIGIN,
                                   json={"all": True})
            listing = client.get("/api/icloud/photos").get_json()
            # Nothing new upstream: a second sync must not re-import the album.
            resynced = client.post("/api/icloud/sync", headers=SAME_ORIGIN, json={})

        assert connected.status_code == 200, connected.get_data(as_text=True)
        assert connected.get_json()["album"] == "Family"
        assert config.get("icloud_connected") is True

        assert imported.get_json()["imported"] == 2, imported.get_data(as_text=True)
        assert all(p["imported"] for p in listing["photos"]), listing
        assert resynced.get_json()["imported"] == 0, resynced.get_data(as_text=True)

        added = set(os.listdir(vapp.OUTPUT_DIR)) - before
        assert len(added) == 2, added
        assert all(name.startswith("icloud_") for name in added), added

        # The imported photos are ordinary gallery photos — which is what makes
        # the slideshow pick them up with no further wiring.
        gallery = {image["filename"] for image in client.get("/api/images").get_json()}
        assert added <= gallery, added - gallery

        # A photo deleted from the gallery comes back on the next sync.
        victim = sorted(added)[0]
        client.delete(f"/api/images/{victim}", headers=SAME_ORIGIN)

        with fake_upstream(_icloud_upstream()):
            recovered = client.post("/api/icloud/sync", headers=SAME_ORIGIN, json={})
        assert recovered.get_json()["imported"] == 1
    finally:
        for name in set(os.listdir(vapp.OUTPUT_DIR)) - before:
            os.remove(os.path.join(vapp.OUTPUT_DIR, name))
        _forget_icloud()


def test_disconnecting_forgets_the_album_but_keeps_the_photos():
    client = _paired_client()
    _forget_icloud()
    before = set(os.listdir(vapp.OUTPUT_DIR))

    try:
        with fake_upstream(_icloud_upstream(photo_guids=("ONLY-1",))):
            client.post("/api/icloud/connect", headers=SAME_ORIGIN, json={
                "url": f"https://www.icloud.com/sharedalbum/#{ICLOUD_TOKEN}",
                "import_all": True})
        added = set(os.listdir(vapp.OUTPUT_DIR)) - before
        assert len(added) == 1, added

        assert client.post("/api/icloud/disconnect",
                           headers=SAME_ORIGIN, json={}).status_code == 200
        assert config.get("icloud_connected") is False
        assert config.get("icloud_album_token") == ""
        assert vapp.icloud_ledger.count == 0
        # The photos are the owner's now, whatever happens to the album.
        assert set(os.listdir(vapp.OUTPUT_DIR)) - before == added
    finally:
        for name in set(os.listdir(vapp.OUTPUT_DIR)) - before:
            os.remove(os.path.join(vapp.OUTPUT_DIR, name))
        _forget_icloud()
def _fake_nmcli(responses):
    """Stand in for `nmcli`, answering by the subcommand being asked about.

    `responses` maps a substring of the argument list to the stdout to return.
    """
    def fake(*args, **kwargs):
        joined = " ".join(args)
        for key, stdout in responses.items():
            if key in joined:
                return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return fake


def test_boot_check_reads_the_link_not_the_scan():
    """A connected device with an empty scan list is connected.

    The regression: `nmcli dev wifi` lists what a scan has seen, and after a
    cold boot that list is empty for minutes while the interface is already
    associated. The boot check read the emptiness as "the saved network is
    unreachable", tore the working link down, and raised the pairing hotspot
    on a device that had been holding a DHCP lease the whole time.
    """
    original = vapp.nmcli
    try:
        vapp.nmcli = _fake_nmcli({
            # Associated, with a lease — but nothing has scanned yet.
            "device status": "wlan0:wifi:connected:EXT0099\nlo:loopback:unmanaged:\n",
            "dev wifi": "",
            "802-11-wireless.ssid": "802-11-wireless.ssid:EXT0099\n",
        })
        assert vapp.get_active_ssid() == "EXT0099"
    finally:
        vapp.nmcli = original


def test_disconnected_link_is_still_reported_as_disconnected():
    """The fix must not make everything look connected."""
    original = vapp.nmcli
    try:
        vapp.nmcli = _fake_nmcli({
            "device status": "wlan0:wifi:disconnected:\nlo:loopback:unmanaged:\n",
        })
        assert vapp.get_active_ssid() is None
    finally:
        vapp.nmcli = original


def test_our_own_hotspot_profile_is_not_a_network():
    """`Vignette-Hotspot` is the profile name, not somewhere we joined."""
    from services import device_id
    assert device_id.is_own_ap("Vignette-Hotspot") is True
    assert device_id.is_own_ap(device_id.ap_ssid()) is True
    assert device_id.is_own_ap("Vignette-Setup") is True
    # A network the owner happens to have named similarly is still theirs.
    assert device_id.is_own_ap("Vignette-Home") is False
    assert device_id.is_own_ap("EXT0099") is False


def test_boot_adopts_a_live_network_instead_of_pairing():
    """Being on the wrong network is not a reason to go off the air.

    Raising the pairing hotspot puts wlan0 into AP mode, so doing it while the
    device is reachable severs the link to announce that it cannot be reached.
    """
    saved = dict(config._data)
    original_verify = vapp.verify_or_connect_wifi_on_boot
    original_active = vapp.get_active_ssid
    try:
        config.update({"wifi_ssid": "OldRouter", "setup_hotspot_fallback": True})
        vapp.verify_or_connect_wifi_on_boot = lambda: False   # saved SSID gone
        vapp.get_active_ssid = lambda: "NewRouter"            # but we are online

        assert vapp.resolve_boot_state() is True
        assert config.get("setup_complete") is True
        assert config.get("wifi_ssid") == "NewRouter"
    finally:
        vapp.verify_or_connect_wifi_on_boot = original_verify
        vapp.get_active_ssid = original_active
        config.update(saved)


def test_production_unit_never_drops_itself_into_pairing():
    """setup_hotspot_fallback=False keeps a finished unit out of setup mode."""
    saved = dict(config._data)
    original_verify = vapp.verify_or_connect_wifi_on_boot
    original_active = vapp.get_active_ssid
    try:
        config.update({"wifi_ssid": "EXT0099", "setup_hotspot_fallback": False})
        vapp.verify_or_connect_wifi_on_boot = lambda: False   # genuinely offline
        vapp.get_active_ssid = lambda: None

        assert vapp.resolve_boot_state() is True
        assert config.get("setup_complete") is True           # no pairing portal
        assert config.get("wifi_ssid") == "EXT0099"           # credentials kept
    finally:
        vapp.verify_or_connect_wifi_on_boot = original_verify
        vapp.get_active_ssid = original_active
        config.update(saved)


def test_unpaired_device_still_raises_the_hotspot():
    """The opt-out must not strand a device that has nothing to retry."""
    saved = dict(config._data)
    original_active = vapp.get_active_ssid
    try:
        config.update({"wifi_ssid": "", "setup_hotspot_fallback": False})
        vapp.get_active_ssid = lambda: None

        assert vapp.resolve_boot_state() is False
        assert config.get("setup_complete") is False
    finally:
        vapp.get_active_ssid = original_active
        config.update(saved)


def test_watchdog_honours_the_production_opt_out():
    """With the fallback off, a dead link retries instead of raising the AP."""
    from services import net_watchdog as nw

    saved = dict(config._data)
    try:
        config.update({"wifi_ssid": "EXT0099", "setup_hotspot_fallback": False,
                       "setup_complete": True})
        calls = []
        watchdog = nw.NetWatchdog()
        watchdog.init(
            config,
            active_ssid=lambda: None,                   # link down throughout
            start_ap=lambda: calls.append("start_ap"),
            stop_ap=lambda: calls.append("stop_ap"),
            scan_wifi=lambda: calls.append("scan"),
            show_pairing_screen=lambda: calls.append("pairing_screen"),
            connect_saved=lambda: calls.append("retry") or False,
            is_pairing_in_progress=lambda: False,
        )

        # Drive the loop directly: past the grace period, past the retry gap.
        watchdog._down_since = 0
        original_poll, original_stop = nw.POLL_SECONDS, watchdog._stop
        try:
            nw.POLL_SECONDS = 0
            # One pass, then stop.
            passes = [0]

            class _OneShot:
                def is_set(self): return passes[0] > 1
                def wait(self, _): passes[0] += 1; return False
                def set(self): passes[0] = 99
                def clear(self): passes[0] = 0

            watchdog._stop = _OneShot()
            watchdog._run()
        finally:
            nw.POLL_SECONDS = original_poll
            watchdog._stop = original_stop

        assert "start_ap" not in calls, "a production unit raised the hotspot"
        assert "pairing_screen" not in calls
        assert "retry" in calls, "it stopped trying to get back on the network"
        assert config.get("setup_complete") is True
    finally:
        config.update(saved)


def _png_bytes(colour=(10, 120, 200)):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), colour).save(buffer, "PNG")
    buffer.seek(0)
    return buffer


def test_upload_token_lets_a_shortcut_in_and_nothing_else():
    """The credential that lives in an automation on somebody's phone.

    It has to be enough to send a photo, and not enough to do anything else —
    a phone is lost more often than a password is stolen.
    """
    from services import upload_token

    client = _paired_client()                       # to mint it
    anonymous = app.test_client()                   # the Shortcut: no cookie
    before = set(os.listdir(vapp.OUTPUT_DIR))

    minted = client.post("/api/upload-token", headers=SAME_ORIGIN, json={})
    token = minted.get_json()["token"]
    assert token.startswith("vgn_")

    try:
        # …and the token is enough to upload.
        sent = anonymous.post(
            "/api/upload", headers={"Authorization": f"Bearer {token}"},
            content_type="multipart/form-data",
            data={"file": (_png_bytes(), "from-shortcut.png")})
        assert sent.status_code == 200, sent.get_data(as_text=True)
        assert sent.get_json()["displaying"] is False

        # The other header spelling works too — Shortcuts makes a custom
        # header easier to fill in than Authorization.
        second = anonymous.post(
            "/api/upload", headers={"X-Upload-Token": token},
            content_type="multipart/form-data",
            data={"file": (_png_bytes(), "from-shortcut.png")})
        assert second.status_code == 200

        # It is not enough for anything else.
        for path in ("/api/config", "/api/reset", "/api/page/switch",
                     "/api/system/reboot", "/api/icloud/connect"):
            blocked = anonymous.post(path, headers={"Authorization": f"Bearer {token}"},
                                     json={})
            assert blocked.status_code == 401, (path, blocked.status_code)
        assert anonymous.get(
            "/api/config", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401

        # A wrong token is no token.
        for bad in (token + "x", token[:-1], "vgn_nonsense", ""):
            refused = anonymous.post(
                "/api/upload", headers={"Authorization": f"Bearer {bad}"},
                content_type="multipart/form-data",
                data={"file": (_png_bytes(), "nope.png")})
            assert refused.status_code == 401, bad

        # Revoking stops it dead, and the status endpoint never hands it back.
        assert "token" not in client.get("/api/upload-token").get_json()
        assert client.delete("/api/upload-token", headers=SAME_ORIGIN).status_code == 200
        assert upload_token.is_configured(config) is False
        after_revoke = anonymous.post(
            "/api/upload", headers={"Authorization": f"Bearer {token}"},
            content_type="multipart/form-data",
            data={"file": (_png_bytes(), "revoked.png")})
        assert after_revoke.status_code == 401
    finally:
        for name in set(os.listdir(vapp.OUTPUT_DIR)) - before:
            os.remove(os.path.join(vapp.OUTPUT_DIR, name))
        upload_token.revoke(config)


def test_phone_sync_url_carries_the_token_and_nothing_more():
    """The address a Shortcuts automation is pointed at.

    Building a daily automation on a phone screen means typing every field by
    hand, and "add a header, key Authorization, value Bearer <64 characters>"
    is the step people abandon. So the same credential is allowed in the path.
    It must open exactly what the header form opens and no more — a token with
    anything appended is a different string and must not match.
    """
    from services import upload_token

    client = _paired_client()
    anonymous = app.test_client()
    before = set(os.listdir(vapp.OUTPUT_DIR))

    minted = client.post("/api/upload-token", headers=SAME_ORIGIN, json={}).get_json()
    token = minted["token"]
    assert minted["sync_url"].endswith(f"/api/upload/t/{token}")

    try:
        sent = anonymous.post(f"/api/upload/t/{token}",
                              content_type="multipart/form-data",
                              data={"file": (_png_bytes(), "from-automation.png")})
        assert sent.status_code == 200, sent.get_data(as_text=True)

        # A suffix is not a match, however valid the prefix is.
        for path in (f"/api/upload/t/{token}/batch",
                     f"/api/upload/t/{token}/../config",
                     f"/api/upload/t/{token}x",
                     "/api/upload/t/vgn_nonsense"):
            refused = anonymous.post(path, content_type="multipart/form-data",
                                     data={"file": (_png_bytes(), "nope.png")})
            assert refused.status_code in (401, 404), (path, refused.status_code)

        # A GET is somebody who scanned the QR code with their camera. It has
        # to answer with the help page rather than "method not allowed" in raw
        # JSON — and it must perform nothing at all.
        settled = set(os.listdir(vapp.OUTPUT_DIR))
        landing = anonymous.get(f"/api/upload/t/{token}")
        assert landing.status_code == 200, landing.status_code
        body = landing.get_data(as_text=True)
        assert "<html" in body.lower(), "the scanned address returned raw JSON"
        assert set(os.listdir(vapp.OUTPUT_DIR)) == settled, "a GET changed something"

        # A GET without a real token must not look like a working address.
        assert anonymous.get("/api/upload/t/vgn_nonsense").status_code in (401, 404)

        # And revoking closes both doors, exactly as it closes the header form.
        client.delete("/api/upload-token", headers=SAME_ORIGIN)
        assert anonymous.post(f"/api/upload/t/{token}",
                              content_type="multipart/form-data",
                              data={"file": (_png_bytes(), "after.png")}
                              ).status_code == 401
        assert anonymous.get(f"/api/upload/t/{token}").status_code in (401, 404)
    finally:
        for name in set(os.listdir(vapp.OUTPUT_DIR)) - before:
            os.remove(os.path.join(vapp.OUTPUT_DIR, name))
        upload_token.revoke(config)


def test_the_same_photo_sent_twice_stays_one_photo():
    """A daily automation re-offers everything inside its window.

    "Photos added in the last 1 day", run once a day, hands the frame each
    photo at least twice — and the filename is regenerated by Photos, so
    nothing about the request says it is the same picture. The bytes do.
    """
    from services import upload_token

    client = _paired_client()
    anonymous = app.test_client()
    before = set(os.listdir(vapp.OUTPUT_DIR))
    token = client.post("/api/upload-token", headers=SAME_ORIGIN,
                        json={}).get_json()["token"]
    photo = _png_bytes().read()

    def send(name, blob=photo):
        return anonymous.post(f"/api/upload/t/{token}",
                              content_type="multipart/form-data",
                              data={"file": (io.BytesIO(blob), name)}).get_json()

    try:
        first = send("IMG_0001.png")
        assert first["duplicate"] is False

        again = send("IMG_0001.png")
        assert again["duplicate"] is True
        assert again["filename"] == first["filename"]

        # Photos renames on export, so the name must not be what identifies it.
        renamed = send("IMG_9999.png")
        assert renamed["duplicate"] is True
        assert renamed["filename"] == first["filename"]

        # Exactly one file landed.
        added = set(os.listdir(vapp.OUTPUT_DIR)) - before
        photos = [n for n in added if not n.startswith(".")]
        assert len(photos) == 1, photos

        # Delete it, and the same photo must be uploadable again — otherwise a
        # picture removed from the gallery could never come back.
        os.remove(os.path.join(vapp.OUTPUT_DIR, first["filename"]))
        assert send("IMG_0001.png")["duplicate"] is False
    finally:
        for name in set(os.listdir(vapp.OUTPUT_DIR)) - before:
            os.remove(os.path.join(vapp.OUTPUT_DIR, name))
        upload_token.revoke(config)


def test_upload_token_is_never_disclosed():
    """It is stored hashed, so nothing can hand it back — not even us."""
    from services import upload_token

    client = _paired_client()
    token = client.post("/api/upload-token", headers=SAME_ORIGIN,
                        json={}).get_json()["token"]
    try:
        assert token not in json.dumps(client.get("/api/config").get_json())
        assert token not in json.dumps(client.get("/api/status").get_json())
        for page in ("/settings", "/", "/gallery"):
            assert token not in client.get(page).get_data(as_text=True), page

        # The stored form is a hash, not the token.
        assert config.get("upload_token_hash") != token
        assert upload_token.verify(config, token) is True
        assert upload_token.verify(config, token + "x") is False

        # Minting again replaces it, so an old Shortcut stops working.
        fresh = client.post("/api/upload-token", headers=SAME_ORIGIN,
                            json={}).get_json()["token"]
        assert fresh != token
        assert upload_token.verify(config, token) is False
    finally:
        upload_token.revoke(config)


def test_upload_can_ask_for_the_photo_to_be_shown():
    """"Send it and show it" is one request, and does not block on the panel."""
    from services import upload_token

    client = _paired_client()
    before = set(os.listdir(vapp.OUTPUT_DIR))
    try:
        response = client.post(
            "/api/upload", headers=SAME_ORIGIN, content_type="multipart/form-data",
            data={"file": (_png_bytes(), "show-me.png"), "display": "1"})
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["displaying"] is True

        # The paint runs behind the response; give it a moment to land.
        name = response.get_json()["filename"]
        for _ in range(50):
            if photo_state_current() == name:
                break
            time.sleep(0.1)
        assert photo_state_current() == name, "the photo never reached the panel"
        assert config.get("current_page") == "photo"
    finally:
        for leftover in set(os.listdir(vapp.OUTPUT_DIR)) - before:
            os.remove(os.path.join(vapp.OUTPUT_DIR, leftover))
        upload_token.revoke(config)


def photo_state_current():
    return vapp.photo_state.get("current_image")


def test_update_script_never_waits_for_a_human():
    """Update Software runs with no terminal attached.

    A git credential prompt or SSH's "continue connecting (yes/no)?" asks
    nobody anything there — it just hangs until the request times out fifteen
    minutes later, with the browser spinning the whole time.
    """
    body = open(os.path.join(REPO, "scripts", "update.sh")).read()
    assert "GIT_TERMINAL_PROMPT=0" in body
    assert "BatchMode=yes" in body

    help_text = run_real(["bash", os.path.join(REPO, "scripts", "update.sh"), "--help"],
                         capture_output=True, text=True, timeout=30)
    assert help_text.returncode == 0
    assert "set -euo" not in help_text.stdout, "help is leaking script body"

    bad = run_real(["bash", os.path.join(REPO, "scripts", "update.sh"), "--nonsense"],
                   capture_output=True, text=True, timeout=30)
    assert bad.returncode == 2, bad.stdout + bad.stderr


def test_update_diagnoses_an_ssh_remote_it_cannot_use():
    """The failure a device in the field actually hits.

    The checkout belongs to the service account — a system user with no home
    to keep an SSH key in — so an `origin` on SSH fails with "Host key
    verification failed" no matter how well SSH works for the login account.
    Git's own message says nothing about any of that, so the script has to.

    Nothing here reaches the network: github.invalid fails at DNS, which is
    also what makes it stand in for a device that cannot be fixed
    automatically. `origin` must survive unchanged in that case.
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copy(os.path.join(REPO, "scripts", "update.sh"),
                    os.path.join(tmp, "scripts", "update.sh"))

        # This sandbox injects url.insteadOf rewrites through GIT_CONFIG_*,
        # which would quietly turn the SSH remote back into an HTTPS one and
        # test nothing at all.
        env = {**os.environ, "GIT_CONFIG_COUNT": "0", "HOME": "/nonexistent"}

        def git(*args):
            return run_real(["git", "-C", tmp, *args], capture_output=True,
                            text=True, timeout=30, env=env)

        git("init", "-q", "-b", "main", ".")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        with open(os.path.join(tmp, "file.txt"), "w") as handle:
            handle.write("x\n")
        git("add", "-A")
        git("commit", "-qm", "base")
        git("remote", "add", "origin", "git@github.invalid:owner/repo.git")

        result = run_real(["bash", os.path.join(tmp, "scripts", "update.sh")],
                          capture_output=True, text=True, timeout=120, env=env)
        output = result.stdout + result.stderr

        assert result.returncode != 0, output
        assert "SSH" in output, output
        # It must name the account that actually does the fetching, and not
        # leave the reader thinking their own ~/.ssh key is being used.
        assert "~/.ssh" in output, output
        assert "deploy key" in output, output
        # An unreachable replacement must not be written over a working one.
        assert git("config", "--get", "remote.origin.url").stdout.strip() == \
            "git@github.invalid:owner/repo.git"


def test_update_diagnoses_a_local_ref_with_no_commit_behind_it():
    """A dead branch ref blocks every fetch, and does not look like one.

    Git checks all local refs while fetching, so one branch pointing at a
    commit the object database no longer has fails the whole update with
    "fatal: bad object refs/heads/<name>" and "did not send all necessary
    objects". Both read like a network fault; neither is. This happened on a
    real device to a branch whose work had already been merged, and the script
    reported "could not reach origin" when origin was perfectly reachable.
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_CONFIG_COUNT": "0", "HOME": "/nonexistent"}
        upstream = os.path.join(tmp, "origin.git")
        work = os.path.join(tmp, "work")

        def git(*args, cwd=work):
            return run_real(["git", "-C", cwd, *args], capture_output=True,
                            text=True, timeout=30, env=env)

        run_real(["git", "init", "-q", "--bare", "-b", "main", upstream],
                 capture_output=True, text=True, timeout=30, env=env)
        run_real(["git", "clone", "-q", upstream, work],
                 capture_output=True, text=True, timeout=30, env=env)
        os.makedirs(os.path.join(work, "scripts"), exist_ok=True)
        shutil.copy(os.path.join(REPO, "scripts", "update.sh"),
                    os.path.join(work, "scripts", "update.sh"))
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        git("add", "-A")
        git("commit", "-qm", "base")
        git("branch", "-M", "main")
        git("push", "-q", "-u", "origin", "main")

        # A branch ref naming a commit that was never here.
        dead = os.path.join(work, ".git", "refs", "heads", "merged-and-gone")
        with open(dead, "w") as handle:
            handle.write("0" * 39 + "1\n")

        script = os.path.join(work, "scripts", "update.sh")
        blocked = run_real(["bash", script], cwd=work, capture_output=True,
                           text=True, timeout=120, env=env)
        output = blocked.stdout + blocked.stderr
        assert blocked.returncode == 1, output
        assert "merged-and-gone" in output, output
        assert "--repair-refs" in output, output
        # The old message blamed the network for this. It must not any more.
        assert "could not reach origin" not in output, output
        assert "WiFi" not in output, output

        repaired = run_real(["bash", script, "--repair-refs"], cwd=work,
                            capture_output=True, text=True, timeout=120, env=env)
        output = repaired.stdout + repaired.stderr
        assert repaired.returncode == 0, output
        assert "Fetching from origin" in output, output
        refs = git("for-each-ref", "--format=%(refname)").stdout
        assert "merged-and-gone" not in refs, refs
        assert "refs/heads/main" in refs, refs

        # And a healthy checkout is unaffected by the new check.
        healthy = run_real(["bash", script], cwd=work, capture_output=True,
                           text=True, timeout=120, env=env)
        assert healthy.returncode == 0, healthy.stdout + healthy.stderr


def test_update_never_stops_at_a_password_prompt():
    """The restart step has no password available to it, ever.

    From the web console there is no terminal to type into. Run by hand as the
    service account there is nothing to type either — it is a --system account
    created with no password. So a sudo that asks is a sudo that hangs or
    fails, after the update has already been applied.

    This happened: `sudo systemctl is-enabled vignette` is not in the service
    account's sudoers allowlist, so a read-only "is it running?" check turned
    into an unanswerable prompt. The stand-in sudo below fails loudly if the
    script ever calls it interactively.
    """
    import shutil

    script = os.path.join(REPO, "scripts", "update.sh")
    with tempfile.TemporaryDirectory() as tmp:
        binf = os.path.join(tmp, "bin")
        os.makedirs(binf)

        def stub(name, body):
            path = os.path.join(binf, name)
            with open(path, "w") as handle:
                handle.write("#!/bin/sh\n" + body)
            os.chmod(path, 0o755)

        # Force the sudo branch even when the tests run as root.
        stub("id", 'case "$1" in -u) echo 1000 ;; *) echo vignette ;; esac\n')
        stub("systemctl", 'exit 0\n')
        stub("systemd-run", 'exit 0\n')
        stub("sudo", 'if [ "$1" = "-n" ]; then shift; echo "SUDO-N: $*"; exit 0; fi\n'
                     'echo "INTERACTIVE-SUDO: $*"; exit 1\n')

        env = {**os.environ, "GIT_CONFIG_COUNT": "0",
               "PATH": binf + os.pathsep + os.environ["PATH"]}
        upstream = os.path.join(tmp, "origin.git")
        work = os.path.join(tmp, "work")

        def git(*args, cwd=work):
            return run_real(["git", "-C", cwd, *args], capture_output=True,
                            text=True, timeout=30, env=env)

        run_real(["git", "init", "-q", "--bare", "-b", "main", upstream],
                 capture_output=True, text=True, timeout=30, env=env)
        run_real(["git", "clone", "-q", upstream, work],
                 capture_output=True, text=True, timeout=30, env=env)
        os.makedirs(os.path.join(work, "scripts"), exist_ok=True)
        shutil.copy(script, os.path.join(work, "scripts", "update.sh"))
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        git("add", "-A")
        git("commit", "-qm", "base")
        git("branch", "-M", "main")
        git("push", "-q", "-u", "origin", "main")

        # Something to actually update to, so the restart step is reached.
        with open(os.path.join(work, "app.py"), "w") as handle:
            handle.write("v2\n")
        git("add", "-A")
        git("commit", "-qm", "next")
        git("push", "-q", "origin", "main")
        git("reset", "-q", "--hard", "HEAD~1")

        result = run_real(["bash", os.path.join(work, "scripts", "update.sh")],
                          cwd=work, capture_output=True, text=True,
                          timeout=120, env=env, stdin=subprocess.DEVNULL)
        output = result.stdout + result.stderr

        assert "INTERACTIVE-SUDO" not in output, output
        assert result.returncode == 0, output
        assert "Restart scheduled" in output, output

        # The restart is only passwordless because it matches the rule
        # install.sh writes into /etc/sudoers.d/vignette. Tie the two together:
        # changing the flags on one side has to fail here rather than in the
        # field, months later, as a prompt nobody can answer.
        restart = [line for line in output.splitlines() if line.startswith("SUDO-N:")]
        assert restart, output
        assert restart[0].startswith(
            "SUDO-N: systemd-run --collect --quiet --unit=vignette-restart-"), restart
        assert "--description=" in restart[0], restart
        assert restart[0].endswith("/bin/sh -c sleep 3; systemctl restart vignette"), restart

        installer = open(os.path.join(REPO, "scripts", "install.sh")).read()
        assert ("NOPASSWD: /usr/bin/systemd-run --collect --quiet "
                "--unit=vignette-restart-* --description=* /bin/sh -c *") in installer


def test_update_hands_over_when_it_rewrites_itself():
    """bash reads a script by byte offset while running it.

    The fast-forward can replace this very file, and bash then carries on
    reading the *new* bytes from the *old* offset — running whatever fragment
    lands there. So a fix to the steps after the fetch never takes effect on
    the run that installs it, and what happens instead is undefined. The script
    hands over to its new copy, and that copy still has to install and restart
    rather than deciding it is already up to date.
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmp:
        binf = os.path.join(tmp, "bin")
        os.makedirs(binf)

        def stub(name, body):
            path = os.path.join(binf, name)
            with open(path, "w") as handle:
                handle.write("#!/bin/sh\n" + body)
            os.chmod(path, 0o755)

        stub("id", 'case "$1" in -u) echo 1000 ;; *) echo vignette ;; esac\n')
        stub("systemctl", "exit 0\n")
        stub("systemd-run", "exit 0\n")
        stub("sudo", 'if [ "$1" = "-n" ]; then shift; echo "SUDO-N: $*"; exit 0; fi\n'
                     'echo "INTERACTIVE-SUDO"; exit 1\n')

        env = {**os.environ, "GIT_CONFIG_COUNT": "0",
               "PATH": binf + os.pathsep + os.environ["PATH"]}
        upstream = os.path.join(tmp, "origin.git")
        work = os.path.join(tmp, "work")
        script = os.path.join(work, "scripts", "update.sh")

        def git(*args):
            return run_real(["git", "-C", work, *args], capture_output=True,
                            text=True, timeout=30, env=env)

        run_real(["git", "init", "-q", "--bare", "-b", "main", upstream],
                 capture_output=True, text=True, timeout=30, env=env)
        run_real(["git", "clone", "-q", upstream, work],
                 capture_output=True, text=True, timeout=30, env=env)
        os.makedirs(os.path.join(work, "scripts"), exist_ok=True)
        shutil.copy(os.path.join(REPO, "scripts", "update.sh"), script)
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        git("add", "-A")
        git("commit", "-qm", "base")
        git("branch", "-M", "main")
        git("push", "-q", "-u", "origin", "main")

        # The upstream commit changes update.sh itself — the case that breaks.
        with open(script, "a") as handle:
            handle.write("\n# added upstream\n")
        git("add", "-A")
        git("commit", "-qm", "rewrites the updater")
        git("push", "-q", "origin", "main")
        git("reset", "-q", "--hard", "HEAD~1")

        result = run_real(["bash", script], cwd=work, capture_output=True,
                          text=True, timeout=120, env=env,
                          stdin=subprocess.DEVNULL)
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "continuing with the new version" in output, output
        # The hand-over must not read as "nothing to do" and skip the restart.
        assert "Already up to date — nothing to install" not in output, output
        assert "Restart scheduled" in output, output
        # And exactly one hand-over: no loop.
        assert output.count("continuing with the new version") == 1, output


def test_coming_up_covers_the_next_three_days_and_fills_the_space():
    """What "Coming up" is for, and how much of it gets shown.

    It answers "is there anything on?" — so it covers today and the two days
    after, and beyond that a frame on a wall is not a planner. How many of
    those it shows is decided by the height of the box it is handed, not by a
    number chosen in advance: three entries stretched over 200 pixels read as
    a page with nothing on it.
    """
    from datetime import datetime, timedelta
    from services import renderer

    now = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)

    def at(**kw):
        return {"summary": "x", "start": now + timedelta(**kw)}

    events = [
        at(hours=-3),                 # earlier today: still shown
        at(hours=2),                  # later today
        at(days=1),                   # tomorrow
        at(days=2, hours=8),          # the day after
        at(days=3),                   # one day past the window
        at(days=9),                   # well past it
    ]
    # Deliberately out of order: the window must sort as well as filter.
    shuffled = [events[4], events[2], events[0], events[5], events[3], events[1]]

    inside = renderer._agenda_rows(shuffled, now=now)
    assert len(inside) == 4, [e["start"] for e in inside]
    assert inside == sorted(inside, key=lambda e: e["start"]), "not in time order"
    horizon = (now + timedelta(days=renderer.AGENDA_DAYS - 1)).date()
    assert all(e["start"].date() <= horizon for e in inside)

    # Yesterday is not "coming up", however recently it was.
    assert renderer._agenda_rows([at(days=-1)], now=now) == []
    assert renderer._agenda_rows([], now=now) == []
    assert renderer._agenda_rows(None, now=now) == []
    # An event with no start at all must not crash the page.
    assert renderer._agenda_rows([{"summary": "no date"}], now=now) == []

    # A taller box shows more of the same list. Rendering the two calendar
    # layouts is the honest way to check that, since the capacity comes from
    # the box each view hands over.
    from PIL import Image, ImageDraw
    from services import epd_ui as ui

    def rows_drawn(height):
        img = Image.new("RGB", (400, 480), (255, 255, 255))
        renderer._draw_agenda(ImageDraw.Draw(img), ui.Box(0, 0, 380, height), events)
        # Count the blue ticks: one per row, and the only blue in this box.
        px = img.load()
        ticks = set()
        for y in range(height):
            if px[1, y] == (0, 0, 255):
                ticks.add(y // 8)
        return len(ticks)

    tall = rows_drawn(240)
    short = rows_drawn(90)
    assert tall > short, (tall, short)
    assert short >= 1


def test_an_all_day_event_is_never_given_a_time():
    """A date-only event occupies the day, not the stroke of midnight.

    Published as ``DTSTART;VALUE=DATE:20260818``, an all-day entry has no time
    in it at all. Parsing it lands on 00:00, and printing that read as "Today
    00:00" on the panel — a dentist appointment apparently booked for
    midnight. The flag has to survive the parser, the label and the drawn row.
    """
    from datetime import datetime, timedelta
    from PIL import Image, ImageDraw
    from services import epd_ui as ui, renderer
    from services.calendar_svc import _parse_ical

    ics = ("BEGIN:VCALENDAR\r\n"
           "BEGIN:VEVENT\r\nSUMMARY:Nose check\r\n"
           "DTSTART;VALUE=DATE:{d}\r\nDTEND;VALUE=DATE:{n}\r\nEND:VEVENT\r\n"
           "BEGIN:VEVENT\r\nSUMMARY:Dentist\r\n"
           "DTSTART:{d}T190000Z\r\nEND:VEVENT\r\n"
           "END:VCALENDAR\r\n").format(
               d=datetime.now().strftime("%Y%m%d"),
               n=(datetime.now() + timedelta(days=1)).strftime("%Y%m%d"))

    parsed = {e["summary"]: e for e in _parse_ical(ics, 7)}
    assert parsed["Nose check"]["all_day"] is True
    assert parsed["Dentist"]["all_day"] is False, "a timed event is not all-day"

    # No DTEND at all: an all-day event still covers its whole day, or it
    # disappears from the frame at 00:01 on the day it is happening.
    bare = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Nose check\r\n"
            "DTSTART;VALUE=DATE:{d}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
            ).format(d=datetime.now().strftime("%Y%m%d"))
    assert [e["summary"] for e in _parse_ical(bare, 7)] == ["Nose check"]

    # The label says so, and never invents an hour for it.
    when = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    assert ui.when_label(when, all_day=True) == "Today All day"
    assert "00:00" not in ui.when_label(when, all_day=True)
    assert ui.when_label(when.replace(hour=19), all_day=False) == "Today 19:00"

    # And the drawn row carries it through, rather than the flag being lost
    # somewhere between the parser and the panel.
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    renderer._draw_agenda(ImageDraw.Draw(img), ui.Box(0, 0, 380, 180),
                          [{"summary": "Nose check", "start": when,
                            "all_day": True}])
    plain = Image.new("RGB", (400, 200), (255, 255, 255))
    renderer._draw_agenda(ImageDraw.Draw(plain), ui.Box(0, 0, 380, 180),
                          [{"summary": "Nose check", "start": when}])
    assert img.tobytes() != plain.tobytes(), (
        "the all-day row draws identically to a midnight one — the flag is "
        "not reaching the renderer")


def test_no_view_puts_a_clock_in_its_corner():
    """Asked for three times, once per view, so it is written down.

    The calendar, the split page and the weather full screen each carried the
    fetch time in the top-right of their header. A panel on a wall is read at
    a glance for what day it is and how warm it is; when the reading was
    fetched is a fact about the software, not about the weather.

    The event times in "Coming up" are deliberately still there — those are
    when something starts, which is the entire point of the list.
    """
    source = open(os.path.join(REPO, "web", "services", "renderer.py"),
                  encoding="utf-8").read()

    assert "right_text" not in source, (
        "a header is being given corner text again; if it is a clock, it was "
        "removed on purpose three times")
    assert "weather.get('updated'" not in source
    assert 'weather.get("updated"' not in source

    # ui.header must still *support* it — this is a decision about the pages,
    # not a capability being deleted from the toolkit.
    toolkit = open(os.path.join(REPO, "web", "services", "epd_ui.py"),
                   encoding="utf-8").read()
    assert "right_text=None" in toolkit

    # The agenda's own times stay — now via ui.when_label, which says the
    # start time for a timed event and "All day" for a date-only one.
    assert "ui.when_label(start" in source
    toolkit_when = "def when_label("
    assert toolkit_when in toolkit
    assert "time_label(when)" in toolkit


def test_the_gallery_actions_sit_on_the_picture_not_on_the_filename():
    """Display and Delete overlay the photo, and only the photo.

    The bar was anchored to the tile, whose bottom edge is below the filename
    and the file size — so it was drawn on top of both. Hover hid it on a
    desktop; a phone reports no hover at all and keeps the bar up permanently,
    which is where this was noticed: the filename with a Display button
    printed across it.
    """
    import re

    from PIL import Image

    # The tile markup only exists when there is something in the gallery.
    seeded = os.path.join(vapp.OUTPUT_DIR, "tile-layout-check.png")
    Image.new("RGB", (8, 8), (255, 255, 255)).save(seeded)
    try:
        html = _paired_client().get("/gallery").get_data(as_text=True)
    finally:
        os.remove(seeded)

    # The bar and the picture share a box, and the meta row is outside it.
    # Checked by order rather than by parsing: tile__media opens, the thumb
    # and the actions fall inside it, and tile__meta comes after it closes.
    assert 'class="tile__media"' in html, (
        "tile__media is gone — the actions have nothing to anchor to")
    order = [html.index(f'class="tile__{part}"')
             for part in ("media", "thumb", "actions", "meta")]
    assert order == sorted(order), (
        "the action bar and the filename are not in the order the layout "
        "depends on", order)

    css = open(os.path.join(REPO, "web", "static", "css", "app.css"),
               encoding="utf-8").read()
    # Absolute positioning needs a positioned ancestor; without this rule the
    # actions fall back to .tile and land on the filename again.
    assert re.search(r"\.tile__media\s*\{[^}]*position:\s*relative", css), (
        ".tile__media is not a positioning context")
    actions = re.search(r"\.tile__actions\s*\{([^}]*)\}", css)
    assert actions and "position: absolute" in actions.group(1)
    assert "inset: auto 0 0 0" in actions.group(1)

    # An icon-only action is a 32px square whose grid centres its one child.
    # Giving one a label adds a second child, and the default flow puts it on
    # a second row inside that fixed 32px — which drew "Display" below the
    # button and out the bottom of it.
    grow = re.search(r"\.tile__action--grow\s*\{([^}]*)\}", css)
    assert grow, "the labelled action has lost its own rule"
    assert "grid-auto-flow: column" in grow.group(1), (
        "the label will stack under the icon again")


def test_settings_saves_itself_except_where_it_must_not():
    """Which controls save on their own, and which keep a button.

    A setting that is only written to config.json can be committed the moment
    it settles. One that reaches out — connecting an album, a Drive account, a
    tunnel — cannot: it can fail for reasons that have nothing to do with what
    was typed, and tearing down a tunnel on every keystroke is not a feature.
    """
    html = _paired_client().get("/settings").get_data(as_text=True)

    # These commit themselves, so their buttons are gone.
    for gone in ('id="save-weather"', 'id="save-calendar"', 'id="save-photo"'):
        assert gone not in html, f"{gone} is back; that field autosaves now"

    # These reach out, so they keep theirs.
    for kept in ('id="save-icloud"', 'id="save-gdrive"', 'id="save-remote"'):
        assert kept in html, f"{kept} is missing; that one must not autosave"

    # Every field the autosave wiring names has to exist, or a setting silently
    # stops saving with nothing on screen to say so.
    for field in ("s-rotation", "s-fit-mode", "s-weather-city", "s-weather-units",
                  "s-weather-lang", "s-weather-key",
                  "s-icloud-auto", "s-icloud-interval"):
        assert f'id="{field}"' in html, field

    # The calendar is a list, not a field: its rows are built in the browser
    # from what the server seeds here, and the whole list saves itself.
    assert 'id="cal-list"' in html
    assert 'id="cal-add"' in html
    assert "data-calendars=" in html

    # And the page has somewhere to say what it did.
    assert 'id="save-status"' in html


def test_a_gateway_error_page_is_never_pasted_into_the_interface():
    """A reply that did not come from this device is not an error message.

    Everything the frame answers is JSON. Behind a tunnel, a 502 answers with
    a full HTML error page instead, and V.api used to hand the first 300
    characters of it to the caller — which is how the settings page's save line
    came to read "Not saved \u2014 <!DOCTYPE html> <!--[if lt IE 7]> \u2026".

    This is a source check because the behaviour lives in the browser; it
    guards the shape rather than the rendering, which was verified by driving
    a stubbed 502 through a real page.
    """
    js = open(os.path.join(REPO, "web", "static", "js", "app.js"),
              encoding="utf-8").read()
    assert "text.slice(0, 300)" not in js, "raw response text is reaching callers again"
    assert "describeGateway" in js, "the non-JSON reply has no description path"

    # And the one line of status bounds whatever does reach it.
    page = open(os.path.join(REPO, "web", "templates", "settings.html"),
                encoding="utf-8").read()
    assert "message.length > 120" in page, "the save line prints unbounded text"


def test_static_assets_change_address_when_they_change():
    """An update must not leave the console rendering against its old CSS.

    The stylesheet is served from a fixed path, so a browser that has it cached
    keeps using it after an update and the new markup lands on the old rules.
    That does not read as a caching problem to anybody — it reads as the update
    having broken the page. It happened: a settings page arrived with its rows
    unstacked and a QR code rendered at full column width.
    """
    client = _paired_client()
    html = client.get("/settings").get_data(as_text=True)

    import re
    hrefs = re.findall(r'/static/css/app\.css\?v=(\d+)', html)
    assert hrefs, "app.css is served without a version stamp"

    scripts = re.findall(r'/static/js/\w+\.js\?v=(\d+)', html)
    assert scripts, "the scripts are served without a version stamp"

    # The stamp has to follow the file, or it is decoration.
    css = os.path.join(REPO, "web", "static", "css", "app.css")
    original = os.stat(css).st_mtime
    try:
        os.utime(css, (original + 120, original + 120))
        moved = re.findall(r'/static/css/app\.css\?v=(\d+)',
                           client.get("/settings").get_data(as_text=True))
        assert moved and moved[0] != hrefs[0], (hrefs, moved)
    finally:
        os.utime(css, (original, original))

    # And the versioned URL still serves the file rather than 404ing on the
    # argument it carries.
    fetched = client.get(f"/static/css/app.css?v={hrefs[0]}")
    assert fetched.status_code == 200, fetched.status_code


def test_config_survives_a_reload():
    """Round-trip through the atomic save path."""
    from services.config import Config
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        first = Config(path)
        first.set("weather_city", "Kaohsiung")
        assert Config(path).get("weather_city") == "Kaohsiung"
        # A temp file must never be left next to it.
        assert os.listdir(tmp) == ["config.json"]


# ── Changing the sign-in address ──────────────────────────────────────────

def _pending_otp(email):
    """The code the panel was told to show, read out of the OTP cache."""
    from services import auth_mgr
    return auth_mgr._otp_cache[email]["code"]


def test_account_change_needs_the_current_password():
    client = _paired_client()
    denied = client.post("/auth/account", headers=SAME_ORIGIN, json={
        "email": "new@example.com", "password": "not-the-password"})
    assert denied.status_code == 401
    # And it must not read as an expired session: the console only bounces to
    # the login page when the server names a redirect, and a wrong password
    # here has to leave the user on the form they were filling in.
    assert "redirect" not in denied.get_json()
    assert config.get("admin_email") == "test@example.com"


def test_account_change_needs_the_code_from_the_panel():
    client = _paired_client()
    started = client.post("/auth/account", headers=SAME_ORIGIN, json={
        "email": "new@example.com", "password": "test-password"})
    assert started.status_code == 200
    assert started.get_json()["needs_otp"] is True

    # Nothing is written until the code comes back.
    assert config.get("admin_email") == "test@example.com"

    wrong = client.post("/auth/account-verify", headers=SAME_ORIGIN,
                        json={"code": "000000"})
    assert wrong.status_code == 401
    assert config.get("admin_email") == "test@example.com"

    ok = client.post("/auth/account-verify", headers=SAME_ORIGIN,
                     json={"code": _pending_otp("new@example.com")})
    assert ok.status_code == 200
    assert config.get("admin_email") == "new@example.com"

    # The new address signs in and the old one does not.
    fresh = app.test_client()
    assert fresh.post("/auth/login", headers=SAME_ORIGIN, json={
        "email": "new@example.com", "password": "test-password"}).status_code == 200
    assert app.test_client().post("/auth/login", headers=SAME_ORIGIN, json={
        "email": "test@example.com", "password": "test-password"}).status_code == 401


def test_account_change_is_closed_to_a_signed_out_browser():
    _paired_client()                      # sets the credential, discards the session
    anonymous = app.test_client()
    denied = anonymous.post("/auth/account", headers=SAME_ORIGIN, json={
        "email": "attacker@example.com", "password": "test-password"})
    assert denied.status_code == 401
    # This one *is* the session wall, so it says where to go.
    assert denied.get_json()["redirect"] == "/auth/login"
    assert config.get("admin_email") == "test@example.com"


def test_account_password_change_takes_effect():
    client = _paired_client()
    client.post("/auth/account", headers=SAME_ORIGIN, json={
        "email": "test@example.com", "password": "test-password",
        "new_password": "a-longer-password"})
    client.post("/auth/account-verify", headers=SAME_ORIGIN,
                json={"code": _pending_otp("test@example.com")})

    fresh = app.test_client()
    assert fresh.post("/auth/login", headers=SAME_ORIGIN, json={
        "email": "test@example.com", "password": "a-longer-password"}).status_code == 200
    assert app.test_client().post("/auth/login", headers=SAME_ORIGIN, json={
        "email": "test@example.com", "password": "test-password"}).status_code == 401


def test_the_whole_site_opens_on_one_theme():
    """Dark by default, and the same default on every page.

    The sign-in and pairing pages carried their own hardcoded palette, so the
    front door was a different colour from the rooms behind it and the theme
    switch had no effect until you were already through it. They share one
    pre-paint script now. It must not consult the device's night mode either:
    the theme is what was chosen here and nothing else.
    """
    client = _paired_client()
    for page in ("/settings", "/", "/gallery"):
        html = client.get(page).get_data(as_text=True)
        assert 'data-theme="dark"' in html, page
        assert "prefers-color-scheme" not in html, page

    # Signed out: the kiosk shell, which is a different template entirely.
    login = app.test_client().get("/auth/login").get_data(as_text=True)
    assert 'data-theme="dark"' in login
    assert "vignette-theme" in login, "kiosk pages ignore the stored preference"
    assert "prefers-color-scheme" not in login

    script = os.path.join(REPO, "web", "static", "js", "ui.js")
    with open(script, encoding="utf-8") as handle:
        assert "prefers-color-scheme" not in handle.read()

    # The kiosk stylesheet must be theme-driven, not a fixed palette: every
    # colour comes from the token set, so light and dark both work.
    css = open(os.path.join(REPO, "web", "static", "css", "kiosk.css"),
               encoding="utf-8").read()
    assert "rgba(255, 255, 255" not in css, "kiosk.css has hardcoded whites again"


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
