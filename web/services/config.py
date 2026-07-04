"""
Vignette configuration management.
Stores settings in a JSON file.

Hardened: atomic + fsync writes, restrictive file permissions, thread locking,
secret-stripping for client responses, and an explicit allow-list for values
that may be set through the public /api/config endpoint.
"""

import json
import logging
import os
import tempfile
import threading

logger = logging.getLogger("vignette.config")

DEFAULT_CONFIG = {
    "setup_complete": False,
    "wifi_ssid": "",
    "wifi_password": "",

    # Auth System
    "admin_email": "",
    "admin_password_hash": "",
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_email": "",
    "smtp_password": "",

    # Language
    "lang": "en",  # en, zh

    # Current display state
    "current_page": "photo",  # home, widget, photo
    "widget_mode": "weather",  # weather, calendar

    # Weather settings (One Call API 3.0)
    # NOTE: no key is shipped in source — the owner supplies their own during setup.
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
    "gdrive_token_expiry": 0,  # epoch seconds; 0 = unknown
    "gdrive_connected": False,

    # Remote access (opt-in). No token is shipped in source; the owner supplies
    # their own ngrok authtoken and explicitly enables the public tunnel.
    "ngrok_enabled": False,
    "ngrok_token": "",

    # Photo settings
    "photo_rotation": 0,  # 0, 90, 180, 270
    "photo_fit_mode": "fit",  # fit (letterbox) or stretch

    # Slideshow
    "slideshow_active": False,
    "slideshow_photos": [],  # empty = all photos
    "slideshow_interval": 300,  # seconds
    "slideshow_order": "sequential",  # sequential or random

    # Session
    "session_secret": "",
}

# Keys whose values must never be sent to a client (API responses / templates).
# weather_api_key and gdrive_client_secret are intentionally NOT here: they are
# the admin's own credentials, editable on the authenticated settings page.
SECRET_KEYS = {
    "admin_password_hash",
    "session_secret",
    "wifi_password",
    "smtp_password",
    "gdrive_access_token",
    "gdrive_refresh_token",
    "ngrok_token",
}

# Keys a client may set through the public /api/config endpoint. Everything
# else (admin_*, session_secret, setup_complete, tokens, connection state, …)
# is rejected so this endpoint can never be used to seize the device.
USER_SETTABLE_KEYS = {
    "weather_api_key", "weather_city", "weather_units", "weather_lang",
    "calendar_ical_url",
    "gdrive_client_id", "gdrive_client_secret",
    "smtp_server", "smtp_port", "smtp_email", "smtp_password",
    "lang", "current_page", "widget_mode",
    "photo_rotation", "photo_fit_mode",
    "slideshow_active", "slideshow_photos", "slideshow_interval", "slideshow_order",
    "ngrok_enabled", "ngrok_token",
}

# Value validation for the keys that have a constrained domain. Each validator
# returns the coerced value or raises ValueError.
_ENUMS = {
    "weather_units": {"metric", "imperial"},
    "photo_fit_mode": {"fit", "stretch"},
    "slideshow_order": {"sequential", "random"},
    "current_page": {"home", "widget", "photo"},
    "widget_mode": {"weather", "calendar", "split"},
    "lang": {"en", "zh"},
    "weather_lang": {"en", "zh"},
}


def _coerce(key, value):
    if key in _ENUMS:
        if value not in _ENUMS[key]:
            raise ValueError(f"{key} must be one of {sorted(_ENUMS[key])}")
        return value
    if key == "photo_rotation":
        v = int(value)
        if v not in (0, 90, 180, 270):
            raise ValueError("photo_rotation must be 0/90/180/270")
        return v
    if key == "slideshow_interval":
        return max(int(value), 30)
    if key == "smtp_port":
        v = int(value)
        if not (1 <= v <= 65535):
            raise ValueError("smtp_port out of range")
        return v
    if key in ("slideshow_active", "ngrok_enabled"):
        return bool(value)
    if key == "slideshow_photos":
        if not isinstance(value, list):
            raise ValueError("slideshow_photos must be a list")
        return [str(x) for x in value]
    # Remaining allow-listed keys are free-form strings.
    return "" if value is None else str(value)


class Config:
    def __init__(self, config_path):
        self.config_path = config_path
        self._data = dict(DEFAULT_CONFIG)
        self._lock = threading.RLock()
        self.load()

    def load(self):
        with self._lock:
            if not os.path.exists(self.config_path):
                return
            try:
                with open(self.config_path, 'r') as f:
                    saved = json.load(f)
                # Merge onto defaults so newly-added keys always have a value.
                merged = dict(DEFAULT_CONFIG)
                merged.update(saved)
                self._data = merged
                logger.info(f"Config loaded from {self.config_path}")
            except Exception as e:
                # A corrupt file (e.g. power cut mid-write) must NOT silently
                # wipe the user's settings. Preserve the bad file for recovery
                # and log loudly instead of falling through to defaults.
                logger.error(f"Failed to load config ({e}); keeping in-memory "
                             f"values and backing up the bad file.")
                try:
                    os.replace(self.config_path, self.config_path + ".bad")
                    logger.error(f"Corrupt config backed up to {self.config_path}.bad")
                except Exception as be:
                    logger.error(f"Could not back up corrupt config: {be}")

    def save(self):
        """Atomically persist config. Returns True on success, False on failure."""
        with self._lock:
            try:
                directory = os.path.dirname(self.config_path) or "."
                os.makedirs(directory, exist_ok=True)
                # Write to a temp file in the same dir, fsync, then atomically
                # rename over the target so a crash can never truncate it.
                fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp",
                                           dir=directory)
                try:
                    with os.fdopen(fd, 'w') as f:
                        json.dump(self._data, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, self.config_path)
                finally:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                # Secrets live here — keep it owner-only.
                try:
                    os.chmod(self.config_path, 0o600)
                except OSError:
                    pass
                logger.info("Config saved")
                return True
            except Exception as e:
                logger.error(f"Failed to save config: {e}")
                return False

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            return self.save()

    def update(self, data):
        with self._lock:
            self._data.update(data)
            return self.save()

    def filtered_update(self, data):
        """Apply only allow-listed, validated keys from an untrusted dict.

        Returns (applied: dict, rejected: list[str]). Never writes admin/secret
        or lifecycle keys, so it is safe to expose to /api/config.
        """
        applied = {}
        rejected = []
        with self._lock:
            for key, value in (data or {}).items():
                if key not in USER_SETTABLE_KEYS:
                    rejected.append(key)
                    continue
                try:
                    applied[key] = _coerce(key, value)
                except (ValueError, TypeError):
                    rejected.append(key)
            if applied:
                self._data.update(applied)
                self.save()
        return applied, rejected

    def reset(self):
        with self._lock:
            self._data = dict(DEFAULT_CONFIG)
            self.save()
            logger.info("Config reset to defaults")

    def to_dict(self):
        with self._lock:
            return dict(self._data)

    def public_dict(self):
        """Config with every secret value stripped — safe for API/template use."""
        with self._lock:
            return {k: v for k, v in self._data.items() if k not in SECRET_KEYS}

    @property
    def is_setup_complete(self):
        with self._lock:
            return self._data.get("setup_complete", False)
