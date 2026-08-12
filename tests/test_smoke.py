#!/usr/bin/env python3
"""Smoke tests for the Vignette web service.

The e-paper panel and NetworkManager are stubbed out, so this runs anywhere —
no Raspberry Pi, no display, no WiFi. That is the point: the bug where the
Settings page's "Factory Reset" and "Show QR" buttons both raised a TypeError
would have been caught by nothing more than asking every route for a response.

    python3 -m pytest tests/
    python3 tests/test_smoke.py     # also runs standalone, without pytest
"""

import io
import os
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Hardware and system stubs ─────────────────────────────────────────────

def _install_stubs():
    """Replace the Pi-only pieces so the app can be imported off-device."""
    epd_pkg = types.ModuleType("waveshare_epd")
    epd_mod = types.ModuleType("waveshare_epd.epd7in3e")

    class _EPD:
        def init(self): pass
        def Clear(self): pass
        def sleep(self): pass
        def getbuffer(self, image): return b""
        def display(self, buffer): pass

    epd_mod.EPD = _EPD
    epd_pkg.epd7in3e = epd_mod
    sys.modules.setdefault("waveshare_epd", epd_pkg)
    sys.modules.setdefault("waveshare_epd.epd7in3e", epd_mod)

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
        if response.status_code >= 500:
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
