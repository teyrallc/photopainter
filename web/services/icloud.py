"""
iCloud Shared Album support for Vignette.

A shared album that has "Public Website" turned on is published by Apple as a
small JSON API sitting behind the link Photos hands you:

    https://www.icloud.com/sharedalbum/#B0abcdefghijkl

The token after the ``#`` is the whole credential — there is no account to
sign in to and no OAuth dance, which is why this is the one photo source that
works on a device nobody wants to type a password into. Two calls do
everything:

    POST {host}/{token}/sharedstreams/webstream      → the album and its photos
    POST {host}/{token}/sharedstreams/webasseturls   → time-limited asset URLs

Albums live on numbered partitions. Asking the wrong one is normal and is
answered with HTTP 330 and the right host in the body, so the first call
follows that hop once and remembers where it landed.

Everything here is stdlib urllib, like services/gdrive.py — the frame runs on
a Pi Zero and does not need another dependency for two POSTs.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

logger = logging.getLogger("vignette.icloud")

# Any partition answers; a wrong guess is redirected to the right one.
DEFAULT_HOST = "p23-sharedstreams.icloud.com"
API_PATH = "/{token}/sharedstreams/{endpoint}"

# The album JSON is quoted straight back to the browser, so the shape of the
# hosts we are willing to talk to is pinned rather than trusted. Apple serves
# the API from p<NN>-sharedstreams.icloud.com and the assets themselves from
# cvws.icloud-content.com.
_HOST_RE = re.compile(r"^p\d{1,3}-sharedstreams\.icloud\.com$")
_ASSET_HOST_SUFFIXES = (".icloud.com", ".icloud-content.com", ".apple.com")

# Tokens are base62 with a leading letter. Validating before use keeps a hand
# -edited link from turning into a path segment of our own choosing.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{10,64}$")

HTTP_TIMEOUT = 25
DOWNLOAD_TIMEOUT = 90
# A 7.3" panel needs a couple of megapixels; anything past this is a mistake
# or an attack, and the Pi has a very small disk.
MAX_ASSET_BYTES = 40 * 1024 * 1024
# webasseturls takes the guids in one POST, but a 2000-photo album makes for a
# rude request body.
GUID_BATCH = 40

# Albums change slowly and the Pi is slow; hold the last listing briefly so
# opening the picker and then importing does not fetch it twice.
ALBUM_CACHE_SECONDS = 120
_album_cache = {}
_cache_lock = threading.Lock()

BROWSER_HEADERS = {
    "Content-Type": "text/plain",
    "Origin": "https://www.icloud.com",
    "Referer": "https://www.icloud.com/",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}


class ICloudError(Exception):
    """A failure with a cause the owner can act on."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ── The link ─────────────────────────────────────────────────────────────

def parse_album_token(link):
    """Pull the album token out of whatever the owner pasted.

    Photos offers the link in more than one shape depending on where it is
    copied from, and people paste the whole thing including the ``#``:

        https://www.icloud.com/sharedalbum/#B0abcdefghijkl
        https://www.icloud.com/sharedalbum/zh-tw/#B0abcdefghijkl
        https://share.icloud.com/photos/B0abcdefghijkl
        https://share.icloud.com/photos/B0abcdefghijkl#Family
        B0abcdefghijkl

    Where the token sits depends on the shape, and the two disagree: the
    icloud.com link carries it in the fragment, while a share.icloud.com link
    carries it in the path and may put the *album's name* in the fragment. So
    the host decides, rather than "whatever follows the #".
    """
    text = (link or "").strip()
    if not text:
        raise ICloudError("Paste the iCloud shared album link first.", status=400)

    if "://" in text or "icloud.com" in text.lower():
        split = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
        host = (split.hostname or "").lower()
        fragment = split.fragment.split("?")[0].split("&")[0]
        segments = [s for s in split.path.split("/") if s]
        last = segments[-1] if segments else ""
        # A path word is never the token; without this, a link whose fragment
        # went missing resolves to "sharedalbum", which is the right length and
        # the right alphabet and completely wrong.
        if last.lower() in ("sharedalbum", "photos", "sharedstreams"):
            last = ""
        candidate = last if host.startswith("share.") else (fragment or last)
    else:
        candidate = text.lstrip("#")

    candidate = candidate.strip().strip("/")
    if not _TOKEN_RE.match(candidate):
        raise ICloudError(
            "That does not look like an iCloud shared album link. In Photos, "
            "open the album → People → turn on Public Website, then copy the "
            "link it shows.", status=400)
    return candidate


def album_url(token):
    """The canonical web link for a token, for showing back to the owner."""
    return f"https://www.icloud.com/sharedalbum/#{token}"


# ── HTTP ─────────────────────────────────────────────────────────────────

def _post(host, token, endpoint, payload):
    """POST one of the two shared-stream endpoints and parse the reply."""
    url = f"https://{host}" + API_PATH.format(token=token, endpoint=endpoint)
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=BROWSER_HEADERS,
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            # A literal `null` body is an empty answer, not a redirect.
            return (parsed if parsed is not None else {}), None
    except urllib.error.HTTPError as exc:
        if exc.code == 330:
            # "Wrong partition" — the body names the right one.
            return None, _redirect_host(exc)
        raise ICloudError(_http_message(exc), status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise ICloudError(
            "Could not reach iCloud. Check the network connection.") from exc
    except (ValueError, TimeoutError, OSError) as exc:
        raise ICloudError(f"iCloud sent an unreadable reply: {exc}") from exc


def _redirect_host(exc):
    """Read the partition host out of a 330, refusing anything unexpected."""
    host = exc.headers.get("X-Apple-MMe-Host") if exc.headers else None
    if not host:
        try:
            host = (json.loads(exc.read().decode("utf-8")) or {}).get("X-Apple-MMe-Host")
        except Exception:  # noqa: BLE001 - a malformed redirect is just a failure
            host = None
    host = (host or "").strip()
    if not _HOST_RE.match(host):
        raise ICloudError("iCloud redirected the album somewhere unexpected.")
    return host


def _http_message(exc):
    if exc.code in (401, 403):
        return ("iCloud refused the album. Make sure Public Website is still "
                "on for it in Photos.")
    if exc.code == 404:
        return ("iCloud does not know that album. The link may have been "
                "regenerated — copy it from Photos again.")
    if exc.code == 429:
        return "iCloud is rate limiting this album. Try again in a few minutes."
    return f"iCloud returned HTTP {exc.code}."


def _call(token, endpoint, payload, host=None):
    """POST, following the one partition hop iCloud may ask for."""
    host = host or DEFAULT_HOST
    data, redirect = _post(host, token, endpoint, payload)
    if data is not None:
        return data, host
    data, _again = _post(redirect, token, endpoint, payload)
    if data is None:
        raise ICloudError("iCloud kept redirecting the album.")
    return data, redirect


# ── Album contents ───────────────────────────────────────────────────────

def _derivatives(photo):
    """The photo's renditions, largest last.

    Apple keys them by their long edge ("342", "1366", "2048") and mixes in
    named ones such as "PosterFrame" for videos, which sort nowhere sensible.
    """
    sized = []
    for name, derivative in (photo.get("derivatives") or {}).items():
        if not isinstance(derivative, dict) or not derivative.get("checksum"):
            continue
        try:
            edge = int(name)
        except (TypeError, ValueError):
            edge = _as_int(derivative.get("width")) or 0
        sized.append((edge, derivative))
    sized.sort(key=lambda pair: pair[0])
    return [derivative for _, derivative in sized]


def _as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _asset_url(item):
    """Build the download URL for one webasseturls item, host-checked."""
    if not isinstance(item, dict):
        return None
    host = (item.get("url_location") or "").strip()
    path = item.get("url_path") or ""
    if not host or not path.startswith("/"):
        return None
    if not host.lower().endswith(_ASSET_HOST_SUFFIXES):
        logger.warning(f"Ignoring an iCloud asset on an unexpected host: {host!r}")
        return None
    return f"https://{host}{path}"


def _photo_date(photo):
    """A sortable creation date, as ISO text, or ''."""
    for field in ("dateCreated", "batchDateCreated"):
        value = photo.get(field)
        if value:
            return str(value)
    return ""


def fetch_album(token, refresh=False):
    """Everything the interface needs about one shared album.

    Returns ``{"name", "token", "photos": [...]}`` where each photo carries the
    guid, a thumbnail URL and a full-size URL. Videos are skipped: this is a
    photo frame.
    """
    token = parse_album_token(token)

    if not refresh:
        with _cache_lock:
            entry = _album_cache.get(token)
            if entry and (time.time() - entry["timestamp"]) < ALBUM_CACHE_SECONDS:
                return entry["album"]

    stream, host = _call(token, "webstream", {"streamCtag": None})
    if not isinstance(stream, dict):
        raise ICloudError("iCloud sent an album we could not read.")

    raw_photos = [p for p in (stream.get("photos") or [])
                  if isinstance(p, dict) and p.get("photoGuid")
                  and (p.get("mediaAssetType") or "").lower() != "video"]

    # Resolve every rendition's URL in as few round trips as the batch allows.
    checksum_urls = {}
    guids = [p["photoGuid"] for p in raw_photos]
    for start in range(0, len(guids), GUID_BATCH):
        batch = guids[start:start + GUID_BATCH]
        assets, host = _call(token, "webasseturls", {"photoGuids": batch}, host=host)
        for checksum, item in ((assets or {}).get("items") or {}).items():
            url = _asset_url(item)
            if url:
                checksum_urls[checksum] = url

    photos = []
    for photo in raw_photos:
        renditions = _derivatives(photo)
        urls = [checksum_urls.get(d.get("checksum")) for d in renditions]
        available = [(d, u) for d, u in zip(renditions, urls) if u]
        if not available:
            continue
        largest, full_url = available[-1]
        _, thumb_url = available[0]
        photos.append({
            "guid": photo["photoGuid"],
            "caption": (photo.get("caption") or "").strip(),
            "created": _photo_date(photo),
            "width": _as_int(largest.get("width"), 0),
            "height": _as_int(largest.get("height"), 0),
            "bytes": _as_int(largest.get("fileSize"), 0),
            "url": full_url,
            "thumb": thumb_url,
        })

    # Newest first, to match how the gallery orders everything else.
    photos.sort(key=lambda p: p["created"], reverse=True)

    album = {
        "token": token,
        "name": (stream.get("streamName") or "").strip() or "iCloud Shared Album",
        "owner": " ".join(filter(None, [stream.get("userFirstName"),
                                        stream.get("userLastName")])).strip(),
        "photos": photos,
    }
    with _cache_lock:
        _album_cache[token] = {"album": album, "timestamp": time.time()}
        for stale in [t for t, e in _album_cache.items()
                      if time.time() - e["timestamp"] > ALBUM_CACHE_SECONDS * 4]:
            _album_cache.pop(stale, None)

    logger.info(f"iCloud album {album['name']!r}: {len(photos)} photos")
    return album


def forget_album(token=None):
    """Drop the cached listing so the next read is live."""
    with _cache_lock:
        if token:
            _album_cache.pop(token, None)
        else:
            _album_cache.clear()


# ── Downloading ──────────────────────────────────────────────────────────

def download_asset(url, dest_path, max_bytes=MAX_ASSET_BYTES):
    """Stream one asset to disk. Returns True on success.

    The URL is only ever one this module built from an album listing — never
    one a browser handed us — and the host is checked again here so that stays
    true even if a caller gets creative.
    """
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname or \
            not parts.hostname.lower().endswith(_ASSET_HOST_SUFFIXES):
        logger.warning(f"Refusing to download an iCloud asset from {parts.netloc!r}")
        return False

    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]})
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            with open(dest_path, "wb") as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ICloudError("That photo is larger than this frame "
                                          "is willing to download.")
                    handle.write(chunk)
        return True
    except Exception as exc:  # noqa: BLE001 - one bad photo must not stop a sync
        logger.error(f"iCloud download failed: {exc}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def suggested_filename(photo):
    """A gallery filename that reads like a photo, not like a UUID."""
    guid = re.sub(r"[^A-Za-z0-9]", "", photo.get("guid", ""))[:8] or "photo"
    created = str(photo.get("created") or "")
    try:
        # Apple stamps these as 2021-05-30T12:34:56Z; the date is the useful part.
        stamp = datetime.strptime(created[:10], "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        stamp = ""
    return f"icloud_{stamp}_{guid}.jpg" if stamp else f"icloud_{guid}.jpg"


# ── Import ledger ────────────────────────────────────────────────────────

class ImportLedger:
    """Which album photos this frame already has.

    Kept beside config.json rather than inside it: an album can hold thousands
    of photos, and config.json is rewritten in full every time any setting
    changes — including the WiFi password. This file is disposable; losing it
    costs one duplicate import, nothing more.
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._token = ""
        self._imported = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r") as handle:
                data = json.load(handle)
            self._token = data.get("token", "") or ""
            imported = data.get("imported", {})
            self._imported = {k: v for k, v in imported.items()
                              if isinstance(k, str) and isinstance(v, str)}
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001 - a corrupt ledger is not fatal
            logger.warning(f"Could not read the iCloud ledger: {exc}")

    def _save(self):
        directory = os.path.dirname(self.path) or "."
        temporary = os.path.join(directory, ".icloud-ledger.tmp")
        try:
            os.makedirs(directory, exist_ok=True)
            with open(temporary, "w") as handle:
                json.dump({"token": self._token, "imported": self._imported},
                          handle, indent=2)
            os.replace(temporary, self.path)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Could not write the iCloud ledger: {exc}")
            if os.path.exists(temporary):
                os.remove(temporary)

    def bind(self, token):
        """Point the ledger at an album, clearing it if that album changed."""
        with self._lock:
            if self._token == token:
                return
            self._token = token
            self._imported = {}
            self._save()

    def clear(self):
        with self._lock:
            self._token = ""
            self._imported = {}
            self._save()

    def has(self, guid):
        with self._lock:
            return guid in self._imported

    def filename(self, guid):
        with self._lock:
            return self._imported.get(guid)

    def record(self, guid, filename):
        with self._lock:
            self._imported[guid] = filename
            self._save()

    def forget(self, guid):
        with self._lock:
            if self._imported.pop(guid, None) is not None:
                self._save()

    def prune(self, existing_filenames):
        """Drop entries whose file is gone, so a re-sync can bring them back."""
        with self._lock:
            missing = [g for g, name in self._imported.items()
                       if name not in existing_filenames]
            for guid in missing:
                del self._imported[guid]
            if missing:
                self._save()
            return len(missing)

    @property
    def token(self):
        with self._lock:
            return self._token

    @property
    def count(self):
        with self._lock:
            return len(self._imported)

    def guids(self):
        with self._lock:
            return set(self._imported)
