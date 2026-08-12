"""
Vignette configuration management.
Stores settings in a JSON file.
"""

import json
import logging
import os
import tempfile

logger = logging.getLogger("vignette.config")

DEFAULT_CONFIG = {
    "setup_complete": False,
    "wifi_ssid": "",
    "wifi_password": "",

    # Pairing hotspot. The password is minted per device on first use and
    # printed on the e-paper pairing screen; it is never shipped in the source.
    "ap_password": "",

    # Auth System
    "admin_email": "",
    "admin_password_hash": "",

    # Remote access. Without a tunnel the console is only reachable from the
    # same LAN, which is not how a photo frame given as a gift gets used.
    "remote_access_enabled": True,
    "ngrok_authtoken": "",

    # Which Waveshare panel is fitted: 7in3e or 7in3f.
    "epd_model": "7in3e",

    # Language
    "lang": "en",  # en, zh

    # Current display state
    "current_page": "photo",  # home, widget, photo
    "widget_mode": "weather",  # weather, calendar

    # Weather settings
    "weather_api_key": "",
    "weather_city": "",
    "weather_units": "metric",  # metric, imperial
    "weather_lang": "en",

    # Calendar settings
    "calendar_ical_url": "",

    # Google Drive
    "gdrive_client_id": "",
    "gdrive_client_secret": "",
    "gdrive_access_token": "",
    "gdrive_refresh_token": "",
    "gdrive_connected": False,

    # Photo settings
    "photo_rotation": 0,  # 0, 90, 180, 270
    "photo_fit_mode": "fit",  # fit (letterbox) or stretch

    # Slideshow
    "slideshow_active": False,
    "slideshow_photos": [],  # empty = all photos
    "slideshow_interval": 300,  # seconds
    "slideshow_order": "sequential",  # sequential or random

    # How often the data-driven pages (home, widget) are redrawn on the panel,
    # in seconds. 0 disables the refresh loop entirely.
    "auto_refresh_interval": 3600,
}

# Values that must never leave the device in an API response or a template.
# `session_secret` is the worst of them: with it anyone can mint a valid signed
# session cookie and the sign-in wall stops meaning anything.
SECRET_KEYS = frozenset({
    "session_secret",
    "admin_password_hash",
    "wifi_password",
    "weather_api_key",
    "gdrive_client_secret",
    "gdrive_access_token",
    "gdrive_refresh_token",
    "ngrok_authtoken",
    # Not a credential for this interface, but it is the key to the pairing
    # network — it belongs on the panel in front of the owner, not in an API
    # response that a tunnelled browser session can read.
    "ap_password",
})

# Settings a signed-in user may change through POST /api/config. Everything
# else — the admin credential, the session secret, the WiFi pair, the
# setup flag — has its own guarded flow and must not be writable from here.
WRITABLE_KEYS = frozenset(set(DEFAULT_CONFIG) - {
    "setup_complete",
    "wifi_ssid",
    "wifi_password",
    "admin_email",
    "admin_password_hash",
    # Minted by the device, not chosen by the user.
    "ap_password",
})

# Secrets the settings form deliberately submits empty when the user did not
# retype them. Treat "" as "leave it alone" so saving the form does not silently
# wipe a key that is never rendered back into the input.
PRESERVE_IF_BLANK = frozenset({
    "weather_api_key",
    "gdrive_client_secret",
    "ngrok_authtoken",
})

# Substrings that mark a config key as a credential regardless of whether this
# version still declares it. The backstop for keys left behind by an older
# build, and for the next setting somebody adds without updating SECRET_KEYS.
_SECRET_NAME_PARTS = ("password", "secret", "token", "passwd", "_hash", "authtoken")


def is_secret_name(key):
    lowered = key.lower()
    # `..._configured` markers are booleans this module generates, never values.
    if lowered.endswith("_configured"):
        return False
    return any(part in lowered for part in _SECRET_NAME_PARTS)


class Config:
    def __init__(self, config_path):
        self.config_path = config_path
        self._data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    saved = json.load(f)
                self._data.update(saved)
                logger.info(f"Config loaded from {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to load config: {e}")

    def save(self):
        """Persist the config atomically.

        This runs on a device people unplug at the wall. Opening the real file
        with 'w' truncates it first, so a power cut mid-write left a zero-byte
        or half-written config.json — and on the next boot that read as "no
        WiFi, no admin", dropping the owner back into pairing with their
        credentials gone. Write a sibling temp file, flush it to disk, then
        rename: on POSIX the rename is atomic, so a reader sees either the old
        config or the new one and never a partial one.
        """
        directory = os.path.dirname(self.config_path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".tmp")
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(self._data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.config_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise
            logger.info("Config saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def update(self, data):
        self._data.update(data)
        self.save()

    def apply_user_settings(self, data):
        """Apply a POST /api/config payload, ignoring anything not writable.

        Returns the keys that were actually applied so the caller can log or
        report them.
        """
        applied = {}
        for key, value in (data or {}).items():
            if key not in WRITABLE_KEYS:
                continue
            if key in PRESERVE_IF_BLANK and value == "":
                continue
            applied[key] = value
        if applied:
            self.update(applied)
        rejected = sorted(set(data or {}) - set(applied))
        if rejected:
            logger.warning(f"Ignored non-writable config keys: {rejected}")
        return applied

    def reset(self):
        self._data = dict(DEFAULT_CONFIG)
        self.save()
        logger.info("Config reset to defaults")

    def to_dict(self):
        """Every stored value, secrets included. Internal callers only."""
        return dict(self._data)

    def public_dict(self):
        """The config as it is safe to hand to a browser.

        Secrets are dropped rather than masked, and each one is replaced by a
        `<key>_configured` boolean so the interface can still show whether a
        value is set without ever transmitting it.

        Filtering on SECRET_KEYS alone was not enough. A config.json written by
        an older build keeps keys this version no longer declares — `smtp_password`
        is the live example — and those sailed straight through the allowlist
        because they are not in it. Anything *named* like a credential is
        dropped too, so retiring a setting can never quietly start publishing
        whatever a device still has stored under it.
        """
        safe = {}
        for key, value in self._data.items():
            if key in SECRET_KEYS or is_secret_name(key):
                continue
            safe[key] = value
        for key in SECRET_KEYS:
            safe[f"{key}_configured"] = bool(self._data.get(key))
        return safe

    @property
    def is_setup_complete(self):
        return self._data.get("setup_complete", False)
