#!/usr/bin/env python3
"""Tests for services/icloud.py — the iCloud shared-album photo source.

The link is the whole credential, and everything it names is fetched from the
public internet, so most of what is checked here is the boundary: which hosts
this module is willing to talk to, which links it accepts, and what it does
with an album containing things that are not photos.

No network: urlopen is replaced with canned answers.

    python3 -m pytest tests/
    python3 tests/test_icloud.py     # also runs standalone
"""

import base64
import email.message
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO, "web") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "web"))

from services import icloud  # noqa: E402

TOKEN = "B0abcdefghijkl"


# ── A fake shared-album API ───────────────────────────────────────────────

class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _http_error(url, code, body=b"{}", headers=None):
    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(url, code, "boom", message, io.BytesIO(body))


def _photo(guid, media="image", created="2024-03-15T09:00:00Z", caption="A photo"):
    return {
        "photoGuid": guid,
        "caption": caption,
        "dateCreated": created,
        "mediaAssetType": media,
        "derivatives": {
            "342": {"checksum": f"{guid}-small", "width": "342", "height": "228",
                    "fileSize": "20000"},
            "2048": {"checksum": f"{guid}-large", "width": "2048", "height": "1365",
                     "fileSize": "900000"},
        },
    }


class FakeICloud:
    """Answers webstream / webasseturls, optionally redirecting once."""

    def __init__(self, photos=None, redirect_from=None, redirect_to=None,
                 redirect_in_body=False, asset_host="cvws.icloud-content.com"):
        self.photos = photos if photos is not None else [_photo("PHOTO-1")]
        self.redirect_from = redirect_from
        self.redirect_to = redirect_to
        self.redirect_in_body = redirect_in_body
        self.asset_host = asset_host
        self.calls = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)

        # This fake is the *old* API. An album published the old way does not
        # exist as a shared collection, and 404 there is what sends the reader
        # back to these endpoints — so the fake has to say so.
        if "ckdatabasews" in url:
            return _raise(_http_error(url, 404))

        if self.redirect_from and self.redirect_from in url:
            body = json.dumps({"X-Apple-MMe-Host": self.redirect_to}).encode()
            if self.redirect_in_body:
                return _raise(_http_error(url, 330, body))
            return _raise(_http_error(url, 330, b"{}",
                                      {"X-Apple-MMe-Host": self.redirect_to}))

        if url.endswith("/webstream"):
            return _Response(json.dumps({
                "streamName": "Yilin's Album",
                "userFirstName": "Wei", "userLastName": "Weng",
                "photos": self.photos,
            }).encode())

        if url.endswith("/webasseturls"):
            payload = json.loads(request.data.decode())
            items = {}
            for guid in payload["photoGuids"]:
                for suffix in ("small", "large"):
                    items[f"{guid}-{suffix}"] = {
                        "url_location": self.asset_host,
                        "url_path": f"/S/{guid}-{suffix}.jpg?token=abc",
                    }
            return _Response(json.dumps({"items": items}).encode())

        raise AssertionError(f"unexpected request: {url}")


def _raise(exc):
    raise exc


def _with_upstream(fake, fn):
    real = urllib.request.urlopen
    urllib.request.urlopen = fake
    icloud.forget_album()
    try:
        return fn()
    finally:
        urllib.request.urlopen = real
        icloud.forget_album()


# ── A fake shared-collection (CloudKit) API ──────────────────────────────

CK_TOKEN = "077QFwtYRXaOWWS7Aupj_GDIg"
CK_ZONE = {"zoneName": "SharedCollection-66DC9D07", "ownerRecordName": "_abc",
           "zoneType": "REGULAR_CUSTOM_ZONE"}


def _master(name, filename, kind="public.jpeg", fingerprint=None, expires=9999999999):
    """One CPLMaster record, shaped the way CloudKit really returns them."""
    def resource(tag, size):
        return {"value": {
            "fileChecksum": f"{name}-{tag}", "size": size,
            "downloadURL": (f"https://cvws-h2.icloud-content.com/B/{name}-{tag}/"
                            "${f}?o=abc&e=" + str(expires))}}

    return {
        "recordType": "CPLMaster",
        "recordName": name,
        "fields": {
            "filenameEnc": {"value": base64.b64encode(filename.encode()).decode(),
                            "type": "ENCRYPTED_BYTES"},
            "resOriginalFileType": {"value": kind, "type": "STRING"},
            "resOriginalFingerprint": {"value": fingerprint or name, "type": "STRING"},
            "resOriginalWidth": {"value": 1179, "type": "INT64"},
            "resOriginalHeight": {"value": 2556, "type": "INT64"},
            "resOriginalFileSize": {"value": 7447870, "type": "INT64"},
            "originalCreationDate": {"value": 1786892298000, "type": "TIMESTAMP"},
            "resOriginalRes": resource("orig", 7447870),
            "resJPEGThumbRes": resource("thumb", 53303),
        },
    }


class FakeCloudKit:
    """Answers records/resolve and records/query, and counts the pages."""

    def __init__(self, records=None, pages=None, require_login=False,
                 partition="https://p178-ckdatabasews.icloud.com:443",
                 anonymous=True):
        self.records = records if records is not None else [
            _master("REC-1", "IMG_5968.PNG", "public.png"),
            _master("REC-2", "IMG_5966.JPG"),
        ]
        self.pages = pages
        self.require_login = require_login
        self.partition = partition
        self.anonymous = anonymous
        self.calls = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append(url)
        payload = json.loads(request.data.decode()) if request.data else {}

        if "records/resolve" in url:
            access = {"token": "ANON-TOKEN", "tokenTTL": 1200000,
                      "databasePartition": self.partition}
            return _Response(json.dumps({"results": [{
                "shortGUID": {"value": CK_TOKEN},
                "zoneID": CK_ZONE,
                "requireAppleLogin": self.require_login,
                "anonymousPublicAccess": access if self.anonymous else None,
                "share": {"fields": {"cloudkit.title": {"value": "Vignette"}}},
                "ownerIdentity": {"nameComponents": {"givenName": "WeiEn",
                                                     "familyName": "Weng"}},
            }]}).encode())

        if "records/query" in url:
            assert "publicAccessAuthToken=ANON-TOKEN" in url, url
            if self.pages is None:
                return _Response(json.dumps({"records": self.records}).encode())
            index = 0 if not payload.get("continuationMarker") else \
                int(payload["continuationMarker"])
            page = self.pages[index]
            body = {"records": page}
            if index + 1 < len(self.pages):
                body["continuationMarker"] = str(index + 1)
            return _Response(json.dumps(body).encode())

        raise AssertionError(f"unexpected request: {url}")


# ── The link ──────────────────────────────────────────────────────────────

def test_every_shape_of_link_resolves_to_the_same_token():
    """People paste whatever Photos handed them, hash and all."""
    for link in (
        f"https://www.icloud.com/sharedalbum/#{TOKEN}",
        f"https://www.icloud.com/sharedalbum/zh-tw/#{TOKEN}",
        f"  https://www.icloud.com/sharedalbum/#{TOKEN}  ",
        f"www.icloud.com/sharedalbum/#{TOKEN}",
        f"https://share.icloud.com/photos/{TOKEN}",
        # The newer share link puts the album's *name* after the #, so
        # "whatever follows the hash" is the wrong rule.
        f"https://share.icloud.com/photos/{TOKEN}#SummerHoliday2024",
        # Photos now hands out this shape, and it also carries the token in
        # the path. Not knowing it is what made a working link unpasteable.
        f"https://photos.icloud.com/shared/album/{TOKEN}",
        f"photos.icloud.com/shared/album/{TOKEN}",
        f"#{TOKEN}",
        TOKEN,
    ):
        assert icloud.parse_album_token(link) == TOKEN, link

    assert icloud.album_url(TOKEN).endswith(f"#{TOKEN}")


def test_a_token_may_carry_the_whole_base64url_alphabet():
    """Apple has issued tokens with "-" and "_" in them.

    The alphabet was pinned to base62 from the one example anybody had, so
    "077QFwtYRXaOWWS7Aupj_GDIg" — a real link, straight out of Photos — was
    turned away at the door with "that does not look like a link", which is
    both wrong and unfixable by the person reading it.
    """
    for token in ("077QFwtYRXaOWWS7Aupj_GDIg", "A1b2C3d4-e5_F6g7H8",
                  "B0abcdefghijkl", "_" * 10, "-" * 10):
        assert icloud.parse_album_token(token) == token, token
        assert icloud.parse_album_token(
            f"https://photos.icloud.com/shared/album/{token}") == token

    # Widening the alphabet must not widen it to path separators: the token
    # is interpolated into a URL path, and that is the whole reason it is
    # checked at all.
    for bad in ("a" * 9, "a" * 65, "abcdefghij/k", "abcdefghij.k",
                "abcdefghij k", "abcdefghij%2F", "abcdefghij?x=1"):
        try:
            icloud.parse_album_token(bad)
            raise AssertionError(f"{bad!r} should have been refused")
        except icloud.ICloudError:
            pass


def test_a_link_must_have_come_from_icloud():
    """Once "-" is a token character, any URL's last word looks like a token.

    Before the alphabet was widened the token pattern happened to reject
    "example.com/not-an-album"; afterwards it parsed as the album
    "not-an-album" and the frame went off to ask Apple about it. The host is
    what actually decides whether a link is an album link.
    """
    for link in (f"https://www.icloud.com/sharedalbum/#{TOKEN}",
                 f"https://share.icloud.com/photos/{TOKEN}",
                 f"https://photos.icloud.com/shared/album/{TOKEN}",
                 f"https://icloud.com/sharedalbum/#{TOKEN}"):
        assert icloud.parse_album_token(link) == TOKEN, link

    for link in (f"https://example.com/{TOKEN}",
                 f"https://evil.example/photos/{TOKEN}",
                 # A suffix match on "icloud.com" alone would take this one.
                 f"https://icloud.com.evil.example/photos/{TOKEN}",
                 f"https://noticloud.com/sharedalbum/#{TOKEN}",
                 "https://example.com/not-an-album"):
        try:
            icloud.parse_album_token(link)
            raise AssertionError(f"{link!r} should have been refused")
        except icloud.ICloudError as exc:
            assert exc.status == 400, link

    # A bare token has no host to check and is still accepted.
    assert icloud.parse_album_token(TOKEN) == TOKEN


def test_anything_that_is_not_a_token_is_refused():
    """The token becomes a path segment, so it is never taken on trust."""
    for junk in ("", "   ", "https://example.com/album/#../../etc/passwd",
                 "#short", "https://www.icloud.com/sharedalbum/#B0/../x",
                 # A link whose fragment went missing must not resolve to the
                 # path word, which is the right length and the right alphabet.
                 "https://www.icloud.com/sharedalbum/",
                 "https://share.icloud.com/photos/",
                 "https://photos.icloud.com/shared/album/",
                 "https://photos.icloud.com/shared/albums",
                 "not a link at all"):
        try:
            icloud.parse_album_token(junk)
            raise AssertionError(f"{junk!r} should have been refused")
        except icloud.ICloudError:
            pass


# ── Reading an album ──────────────────────────────────────────────────────

def test_an_album_lists_its_photos_with_both_sizes():
    album = _with_upstream(FakeICloud(), lambda: icloud.fetch_album(TOKEN))

    assert album["name"] == "Yilin's Album"
    assert album["owner"] == "Wei Weng"
    assert len(album["photos"]) == 1

    photo = album["photos"][0]
    assert photo["guid"] == "PHOTO-1"
    assert photo["caption"] == "A photo"
    assert photo["width"] == 2048
    # Largest for the panel, smallest for the picker: a Pi Zero should not be
    # pulling 2048px tiles to draw a grid of thumbnails.
    assert photo["url"].endswith("PHOTO-1-large.jpg?token=abc")
    assert photo["thumb"].endswith("PHOTO-1-small.jpg?token=abc")


def test_videos_are_left_in_the_album():
    """It is a photo frame; a video has no frame to show."""
    fake = FakeICloud(photos=[_photo("PHOTO-1"),
                              _photo("MOVIE-1", media="video")])
    album = _with_upstream(fake, lambda: icloud.fetch_album(TOKEN))
    assert [p["guid"] for p in album["photos"]] == ["PHOTO-1"]


def test_photos_come_back_newest_first():
    fake = FakeICloud(photos=[
        _photo("OLD", created="2023-01-02T09:00:00Z"),
        _photo("NEW", created="2024-06-30T09:00:00Z"),
    ])
    album = _with_upstream(fake, lambda: icloud.fetch_album(TOKEN))
    assert [p["guid"] for p in album["photos"]] == ["NEW", "OLD"]


def test_the_partition_redirect_is_followed():
    """Asking the wrong partition is normal; iCloud says where to go."""
    for in_body in (False, True):
        fake = FakeICloud(redirect_from=icloud.DEFAULT_HOST,
                          redirect_to="p52-sharedstreams.icloud.com",
                          redirect_in_body=in_body)
        album = _with_upstream(fake, lambda: icloud.fetch_album(TOKEN))
        assert len(album["photos"]) == 1
        assert any("p52-sharedstreams" in url for url in fake.calls), fake.calls
        # And the second endpoint goes straight to the partition we landed on.
        assert not any(url.endswith("/webasseturls") and icloud.DEFAULT_HOST in url
                       for url in fake.calls), fake.calls


def test_a_redirect_off_apple_is_refused():
    """The redirect names the host we then POST the token to."""
    fake = FakeICloud(redirect_from=icloud.DEFAULT_HOST,
                      redirect_to="evil.example.com")
    try:
        _with_upstream(fake, lambda: icloud.fetch_album(TOKEN))
        raise AssertionError("expected an ICloudError")
    except icloud.ICloudError as exc:
        assert "unexpected" in str(exc).lower(), exc
    assert not any("evil.example.com" in url for url in fake.calls)


def test_assets_on_an_unexpected_host_are_dropped():
    fake = FakeICloud(asset_host="evil.example.com")
    album = _with_upstream(fake, lambda: icloud.fetch_album(TOKEN))
    assert album["photos"] == []


def test_a_missing_album_says_so():
    def gone(request, timeout=None):
        raise _http_error(request.full_url, 404)

    try:
        _with_upstream(gone, lambda: icloud.fetch_album(TOKEN))
        raise AssertionError("expected an ICloudError")
    except icloud.ICloudError as exc:
        assert exc.status == 404
        assert "album" in str(exc).lower(), exc


def test_a_shared_collection_album_is_read_through_cloudkit():
    """The album shape Photos hands out today.

    Apple moved shared albums off the sharedstreams API onto CloudKit shared
    collections. An album published that way answers 404 to every
    sharedstreams call — which is how an album that opened perfectly well in a
    browser came to be reported as "not found" by the frame.
    """
    fake = FakeCloudKit()
    album = _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))

    assert album["name"] == "Vignette"
    assert album["owner"] == "WeiEn Weng"
    assert album["token"] == CK_TOKEN
    assert album["url"] == f"https://photos.icloud.com/shared/album/{CK_TOKEN}"
    assert len(album["photos"]) == 2

    photo = album["photos"][0]
    assert photo["width"] == 1179 and photo["height"] == 2556
    assert photo["bytes"] == 7447870
    # The signed URL carries a ${f} placeholder for the filename.
    assert "${f}" not in photo["url"] and "${f}" not in photo["thumb"]
    assert photo["url"].startswith("https://cvws-h2.icloud-content.com/")
    assert photo["thumb"] != photo["url"], "the thumbnail is the full-size file"

    # Resolve first, then query on the partition it named.
    assert "ckdatabasews.icloud.com/database/1" in fake.calls[0]
    assert "records/resolve" in fake.calls[0]
    assert "p178-ckdatabasews.icloud.com" in fake.calls[1]


def test_the_real_extension_survives_the_trip():
    """A PNG named .jpg is a file whose name disagrees with its bytes."""
    fake = FakeCloudKit(records=[
        _master("REC-P", "IMG_1.PNG", "public.png"),
        _master("REC-J", "IMG_2.JPG", "public.jpeg"),
        _master("REC-H", "IMG_3.HEIC", "public.heic"),
    ])
    album = _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))
    names = sorted(icloud.suggested_filename(p) for p in album["photos"])
    assert [n.rsplit(".", 1)[1] for n in names] == ["heic", "jpg", "png"], names

    # The older API has no filename to go on, so JPEG stays the safe guess.
    assert icloud.suggested_filename({"guid": "X", "created": ""}).endswith(".jpg")


def test_the_same_picture_twice_is_one_photograph():
    """Adding a photo to an album from two devices makes two records."""
    fake = FakeCloudKit(records=[
        _master("REC-1", "IMG_1.JPG", fingerprint="SAME"),
        _master("REC-2", "IMG_1.JPG", fingerprint="SAME"),
        _master("REC-3", "IMG_2.JPG", fingerprint="OTHER"),
    ])
    album = _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))
    assert len(album["photos"]) == 2, [p["caption"] for p in album["photos"]]


def test_video_never_reaches_a_photo_frame():
    fake = FakeCloudKit(records=[
        _master("REC-1", "IMG_1.JPG", "public.jpeg"),
        _master("REC-2", "IMG_2.MOV", "com.apple.quicktime-movie"),
        _master("REC-3", "IMG_3.MP4", "public.mpeg-4"),
    ])
    album = _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))
    assert [p["caption"] for p in album["photos"]] == ["IMG_1.JPG"]


def test_every_page_of_a_long_album_is_read():
    pages = [[_master(f"REC-{i}", f"IMG_{i}.JPG") for i in range(n, n + 3)]
             for n in (0, 3, 6)]
    fake = FakeCloudKit(pages=pages)
    album = _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))
    assert len(album["photos"]) == 9, len(album["photos"])
    assert sum("records/query" in c for c in fake.calls) == 3


def test_an_album_that_needs_an_apple_account_says_so():
    """Shared with named people rather than published: no anonymous read."""
    fake = FakeCloudKit(require_login=True)
    try:
        _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))
        raise AssertionError("expected an ICloudError")
    except icloud.ICloudError as exc:
        assert exc.status == 403
        assert "apple account" in str(exc).lower(), exc
        # It must not then go and ask the old API the same question.
        assert not any("sharedstreams" in c for c in fake.calls)

    # Same when the album resolves but offers no anonymous access at all.
    fake = FakeCloudKit(anonymous=False)
    try:
        _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))
        raise AssertionError("expected an ICloudError")
    except icloud.ICloudError as exc:
        assert exc.status == 403


def test_a_partition_off_apple_is_refused():
    """The partition names the host the next request is sent to."""
    for evil in ("https://evil.example:443", "https://ckdatabasews.icloud.com.evil.test"):
        fake = FakeCloudKit(partition=evil)
        try:
            _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))
            raise AssertionError(f"{evil} should have been refused")
        except icloud.ICloudError as exc:
            assert "unexpected host" in str(exc).lower(), exc


def test_a_listing_is_not_cached_past_its_download_links():
    """Those URLs expire in about a quarter of an hour.

    Serving a cached listing after that hands the gallery a page of
    photographs that every one of them fails to load.
    """
    import time as _time

    soon = int(_time.time()) + 90
    fake = FakeCloudKit(records=[_master("REC-1", "IMG_1.JPG", expires=soon)])
    album = _with_upstream(fake, lambda: icloud.fetch_album(CK_TOKEN))
    assert album["photos"][0]["expires"] == soon

    # Cached, but only for as long as the links inside it are good for.
    real = urllib.request.urlopen
    urllib.request.urlopen = fake
    icloud.forget_album()
    try:
        icloud.fetch_album(CK_TOKEN)
        first = len(fake.calls)
        icloud.fetch_album(CK_TOKEN)
        assert len(fake.calls) == first, "went back to iCloud while still fresh"

        entry = icloud._album_cache[CK_TOKEN]
        assert entry["expires"] <= soon - 60 + 1, (entry["expires"], soon)
        # Wind the clock past the links' expiry.
        entry["expires"] = _time.time() - 1
        icloud.fetch_album(CK_TOKEN)
        assert len(fake.calls) > first, "served links that had already expired"
    finally:
        urllib.request.urlopen = real
        icloud.forget_album()


def test_the_listing_is_cached_briefly():
    """Opening the picker and then importing must not fetch it twice."""
    fake = FakeICloud()
    real = urllib.request.urlopen
    urllib.request.urlopen = fake
    icloud.forget_album()
    try:
        icloud.fetch_album(TOKEN)
        streams = len([u for u in fake.calls if u.endswith("/webstream")])
        icloud.fetch_album(TOKEN)
        assert len([u for u in fake.calls if u.endswith("/webstream")]) == streams
        icloud.fetch_album(TOKEN, refresh=True)
        assert len([u for u in fake.calls if u.endswith("/webstream")]) == streams + 1
    finally:
        urllib.request.urlopen = real
        icloud.forget_album()


# ── Downloading ───────────────────────────────────────────────────────────

def test_downloads_stay_on_apple_hosts():
    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, "photo.jpg")
        for url in ("https://evil.example.com/photo.jpg",
                    "http://cvws.icloud-content.com/photo.jpg",   # not TLS
                    "file:///etc/passwd", ""):
            assert icloud.download_asset(url, dest) is False, url
            assert not os.path.exists(dest), url


def test_an_oversized_asset_is_abandoned():
    body = b"x" * 4096

    def flood(request, timeout=None):
        return _Response(body)

    real = urllib.request.urlopen
    urllib.request.urlopen = flood
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "photo.jpg")
            url = "https://cvws.icloud-content.com/S/photo.jpg"
            assert icloud.download_asset(url, dest, max_bytes=100) is False
            assert not os.path.exists(dest)
            assert icloud.download_asset(url, dest) is True
            assert os.path.getsize(dest) == len(body)
    finally:
        urllib.request.urlopen = real


def test_filenames_read_like_photos():
    assert icloud.suggested_filename(
        {"guid": "ABCDEF12-3456", "created": "2024-03-15T09:00:00Z"}
    ) == "icloud_20240315_ABCDEF12.jpg"
    # A photo with no usable date still gets a stable, safe name.
    name = icloud.suggested_filename({"guid": "ABCDEF12-3456", "created": ""})
    assert name == "icloud_ABCDEF12.jpg"
    assert "/" not in name and ".." not in name


# ── The import ledger ─────────────────────────────────────────────────────

def test_the_ledger_remembers_what_is_already_on_the_frame():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "icloud_album.json")
        ledger = icloud.ImportLedger(path)
        ledger.bind(TOKEN)
        ledger.record("PHOTO-1", "icloud_20240315_PHOTO1.jpg")

        assert ledger.has("PHOTO-1") and not ledger.has("PHOTO-2")
        assert ledger.count == 1

        # It has to survive a restart, or every reboot re-imports the album.
        reloaded = icloud.ImportLedger(path)
        assert reloaded.token == TOKEN
        assert reloaded.filename("PHOTO-1") == "icloud_20240315_PHOTO1.jpg"

        # A photo deleted from the gallery may come back on the next sync.
        assert reloaded.prune({"something-else.jpg"}) == 1
        assert not reloaded.has("PHOTO-1")


def test_pointing_the_ledger_at_another_album_clears_it():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = icloud.ImportLedger(os.path.join(tmp, "icloud_album.json"))
        ledger.bind(TOKEN)
        ledger.record("PHOTO-1", "one.jpg")
        ledger.bind(TOKEN)                       # same album: keep it
        assert ledger.has("PHOTO-1")
        ledger.bind("C1zzzzzzzzzzzz")            # different album: start over
        assert ledger.count == 0


def test_a_corrupt_ledger_is_not_fatal():
    """It is disposable state; losing it costs one duplicate import."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "icloud_album.json")
        with open(path, "w") as handle:
            handle.write("{ this is not json")
        ledger = icloud.ImportLedger(path)
        assert ledger.count == 0
        ledger.bind(TOKEN)
        ledger.record("PHOTO-1", "one.jpg")
        assert icloud.ImportLedger(path).has("PHOTO-1")


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
