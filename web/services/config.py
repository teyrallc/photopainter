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

    # Whether losing the network drops the device back into pairing mode.
    #
    # On a unit still being set up this is the recovery path: the router was
    # replaced, the frame moved house, and there is no keyboard to fix it with.
    # On a finished unit on a wall it is a liability — raising the hotspot puts
    # wlan0 into AP mode, which severs the very link it is trying to report on,
    # so a two-minute router reboot turns into the frame advertising a pairing
    # network and showing the setup screen.
    #
    # Off means the device keeps retrying the saved network quietly and forever,
    # and never takes itself off the air. A device that has never been paired
    # still raises the hotspot regardless — it has nothing else to try.
    "setup_hotspot_fallback": True,

    # Auth System
    "admin_email": "",
    "admin_password_hash": "",

    # Remote access. Without a tunnel the console is only reachable from the
    # same LAN, which is not how a photo frame given as a gift gets used.
    "remote_access_enabled": True,
    # "cloudflare" (fixed address, recommended) | "ngrok" | "none".
    # Defaults to ngrok so a device updating in the field keeps whatever it
    # had; switch it in Settings after running scripts/setup-tunnel.sh.
    "remote_access_provider": "ngrok",
    # The address the owner's DNS points at, e.g. https://yilin.example.com.
    # Used by the Cloudflare provider, and shown on the panel.
    "remote_public_url": "",
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

    # Calendar settings.
    #
    # `calendars` is the real setting: a list of
    #     {"url": ..., "name": ..., "color": "blue"|"red"|"green"|"yellow"}
    # so a household can subscribe to more than one feed and tell them apart
    # on the panel. `calendar_ical_url` is kept as the first calendar's URL —
    # a device updating in the field has one, the setup page writes one, and
    # the two are reconciled in _sync_calendars() so there is never a moment
    # where they disagree.
    "calendars": [],
    "calendar_ical_url": "",

    # Google Drive
    "gdrive_client_id": "",
    "gdrive_client_secret": "",
    "gdrive_access_token": "",
    "gdrive_refresh_token": "",
    "gdrive_connected": False,

    # iCloud Shared Album. The link is the whole credential — there is no
    # account to sign in to — so it is set through /api/icloud/connect, which
    # checks the album answers before storing anything.
    "icloud_album_url": "",
    "icloud_album_token": "",
    "icloud_album_name": "",
    "icloud_connected": False,
    # Pull newly shared photos on a timer, so a photo added on somebody's
    # phone reaches the frame without anyone opening this console.
    "icloud_auto_sync": True,
    "icloud_sync_interval": 3600,   # seconds between checks
    "icloud_last_sync": "",
    "icloud_last_error": "",

    # The credential an iPhone Shortcut carries to send photos in. Stored
    # hashed; the token itself is shown once, when it is minted.
    "upload_token_hash": "",
    "upload_token_created": "",

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
    # Only ever a hash, but it is the stored half of a working credential and
    # being on this list is also what gives the interface its
    # `upload_token_hash_configured` marker.
    "upload_token_hash",
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
    # Minted and revoked through /api/upload-token, which is the only place
    # that ever sees the token itself.
    "upload_token_hash",
    "upload_token_created",
    # Written by /api/icloud/connect once the album has actually answered.
    # Letting these through here would leave the console reporting a
    # connection to an album nothing has ever reached.
    "icloud_album_url",
    "icloud_album_token",
    "icloud_album_name",
    "icloud_connected",
    "icloud_last_sync",
    "icloud_last_error",
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


# The panel has six inks and two of them are the paper and the type, so a
# calendar can be told apart by one of four. Blue first because it is what the
# agenda already used, and yellow last because it is the faintest on white.
CALENDAR_COLORS = ("blue", "red", "green", "yellow")


def normalize_calendars(value, legacy_url=""):
    """Clean up whatever is stored (or posted) as the calendar list.

    Anything without a URL is dropped, names are trimmed, and a colour that is
    not one this panel can print is replaced by the next unused one — so a
    hand-edited config.json cannot put a colour on the screen that the display
    has no ink for.
    """
    entries = []
    for item in (value if isinstance(value, list) else []):
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        # A feed is fetched with urllib, which will happily open file:// and
        # ftp:// as well. Nothing useful comes back from those and a config
        # file is something a person edits, so the scheme is pinned here
        # rather than left to whatever urlopen is willing to do.
        if not url.lower().startswith(("http://", "https://")):
            continue
        color = str(item.get("color") or "").strip().lower()
        entries.append({"url": url,
                        "name": str(item.get("name") or "").strip()[:40],
                        "color": color if color in CALENDAR_COLORS else ""})

    # Colours are filled in a second pass, so that what somebody actually
    # chose is reserved before the gaps are filled. Done in one pass, a
    # calendar with no colour ahead of a blue one takes blue for itself and
    # the two arrive on the panel indistinguishable.
    taken = {entry["color"] for entry in entries if entry["color"]}
    for index, entry in enumerate(entries):
        if entry["color"]:
            continue
        entry["color"] = next((c for c in CALENDAR_COLORS if c not in taken),
                              CALENDAR_COLORS[index % len(CALENDAR_COLORS)])
        taken.add(entry["color"])

    # A device that has only ever had the single-URL setting arrives here with
    # an empty list and that URL still in place.
    if not entries and str(legacy_url or "").strip():
        entries.append({"url": legacy_url.strip(), "name": "",
                        "color": CALENDAR_COLORS[0]})
    return entries


class Config:
    def __init__(self, config_path):
        self.config_path = config_path
        self._data = dict(DEFAULT_CONFIG)
        self.load()
        self._sync_calendars()

    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    saved = json.load(f)
                self._data.update(saved)
                # A config written before calendars became a list carries only
                # the single URL. Promote it once, here, where "the key is not
                # in the file" still distinguishes an old config from a list
                # somebody has deliberately emptied.
                if "calendars" not in saved:
                    self._data["calendars"] = normalize_calendars(
                        [], saved.get("calendar_ical_url", ""))
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
        self.update({key: value})

    def update(self, data):
        data = self._calendar_url_edit(dict(data or {}))
        self._data.update(data)
        self._sync_calendars()
        self.save()

    def _calendar_url_edit(self, data):
        """Turn a write to the single URL into an edit of the first calendar.

        The setup page and older builds know one iCal URL and nothing about a
        list. Rewriting the payload here means the list stays the only thing
        that is ever stored, so there is no second source of truth to drift.
        """
        if "calendar_ical_url" not in data or "calendars" in data:
            return data
        url = str(data.pop("calendar_ical_url") or "").strip()
        entries = normalize_calendars(self._data.get("calendars"))
        if not url:
            # Clearing the one URL the caller knows about clears its calendar,
            # and leaves any others alone.
            entries = entries[1:]
        elif entries:
            entries[0] = dict(entries[0], url=url)
        else:
            entries = [{"url": url, "name": "", "color": CALENDAR_COLORS[0]}]
        data["calendars"] = entries
        return data

    def _sync_calendars(self):
        """`calendars` is the setting; `calendar_ical_url` mirrors its first.

        The mirror only ever flows one way. Reconciling in both directions
        looks tidier and is wrong: deleting the first calendar leaves the old
        URL sitting in the mirror, which is indistinguishable from someone
        having just typed it, and the deleted feed comes straight back.

        A write to the single URL is turned into a list edit where it happens
        — see `_calendar_url_edit` — so by the time this runs the list is
        already the truth.
        """
        entries = normalize_calendars(self._data.get("calendars"))
        self._data["calendars"] = entries
        self._data["calendar_ical_url"] = entries[0]["url"] if entries else ""

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
