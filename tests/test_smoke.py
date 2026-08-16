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

    for page in ("/wifi", "/manual", "/settings"):
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
