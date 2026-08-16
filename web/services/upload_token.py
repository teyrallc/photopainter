"""
Upload tokens — the credential an iPhone Shortcut carries.

The console is a session-cookie affair: a browser signs in, gets a cookie, and
every /api/ call rides on it. A Shortcut cannot do that. It has no cookie jar
worth the name, and asking somebody to embed their admin password in an
automation on their phone would hand that automation the whole device —
factory reset, WiFi, the lot.

So there is a second, much smaller credential: a random token that authorises
exactly one thing, sending a photo to the frame. It is stored hashed, shown to
the owner once when it is minted, and revocable on its own without disturbing
the sign-in account.

Hashing note: this is 256 bits of CSPRNG output, not a human-chosen password.
There is no dictionary to run and no rainbow table to build, so a single SHA-256
is the right cost — the pbkdf2 the admin password uses would buy nothing here
and a Pi Zero would pay for it on every upload.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime

logger = logging.getLogger("vignette.upload_token")

# Long enough that guessing is not a threat model, short enough to retype off a
# screen if the copy button fails somebody.
TOKEN_BYTES = 32
TOKEN_PREFIX = "vgn_"

HASH_KEY = "upload_token_hash"
CREATED_KEY = "upload_token_created"


def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint(config):
    """Create a token, store only its hash, and return the token itself.

    The plaintext is returned exactly once — here — and never again. Losing it
    means minting another, which is the trade that keeps a stolen config.json
    from carrying a working upload credential.
    """
    token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    config.update({
        HASH_KEY: _digest(token),
        CREATED_KEY: datetime.now().isoformat(timespec="seconds"),
    })
    logger.info("Upload token minted")
    return token


def revoke(config):
    """Forget the token. Any Shortcut carrying it stops working immediately."""
    config.update({HASH_KEY: "", CREATED_KEY: ""})
    logger.info("Upload token revoked")


def is_configured(config):
    return bool(config.get(HASH_KEY))


def created_at(config):
    return config.get(CREATED_KEY, "") or ""


def verify(config, presented):
    """Is `presented` the token this device minted?

    compare_digest so the check cannot be walked one character at a time
    through response timing — the endpoint this guards answers to the public
    internet through the tunnel.
    """
    stored = config.get(HASH_KEY, "") or ""
    if not stored or not presented:
        return False
    return hmac.compare_digest(stored, _digest(presented))


def from_request(request):
    """Pull the token out of a request, whichever way it was sent.

    `Authorization: Bearer …` is what most tooling reaches for; the explicit
    header is there because Shortcuts' "Get contents of URL" makes a custom
    header the easier of the two to fill in on a phone.
    """
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get("X-Upload-Token", "") or "").strip()
