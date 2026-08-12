"""Stable per-device identity.

Every unit used to advertise the same hotspot — `Vignette-Setup` with the
password `vignette123` written into the source — so the pairing network of any
device was joinable by anyone who had read the repository, and two units in one
house could not be told apart.

The SSID suffix is derived from the board's hardware serial so a given device
always answers to the same name, even after a factory reset. The password is
random and stored in the config: it never needs to be recovered from anywhere
because the pairing screen on the e-paper prints it.
"""

import hashlib
import logging
import re
import secrets
import uuid

logger = logging.getLogger("vignette.device")

SSID_PREFIX = "Vignette"

# Ambiguous glyphs are omitted: this password is read off an e-paper panel and
# typed into a phone, so 0/O and 1/l/I cost more in support than they add in
# entropy. 8 characters from a 31-symbol alphabet is ~39 bits, far beyond what
# a WPA2 handshake attack gets through in the lifetime of a pairing session.
_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
_PASSWORD_LENGTH = 8


def hardware_serial():
    """The board's serial number, or a MAC-derived stand-in off-device."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("serial"):
                    serial = line.split(":", 1)[1].strip()
                    if serial and set(serial) != {"0"}:
                        return serial
    except OSError:
        pass

    # uuid.getnode() falls back to a random value when it cannot read a MAC,
    # which would make the SSID unstable — but only on hardware this product
    # does not ship on.
    return f"{uuid.getnode():012x}"


def device_suffix():
    """Four stable characters that distinguish one unit from another."""
    digest = hashlib.sha256(hardware_serial().encode()).hexdigest()
    return digest[:4].upper()


def ap_ssid():
    return f"{SSID_PREFIX}-{device_suffix()}"


def generate_password():
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))


def ap_credentials(config):
    """(ssid, password) for this device's pairing hotspot.

    The password is minted once and kept. A factory reset clears it and the
    next pairing screen shows a fresh one, which is the behaviour you want
    when a device changes hands.
    """
    ssid = ap_ssid()
    password = config.get("ap_password", "")

    # WPA2 will not accept anything shorter than 8 characters, so a truncated
    # or hand-edited value has to be replaced rather than passed through to a
    # hotspot that then silently fails to start.
    if not password or len(password) < 8:
        password = generate_password()
        config.set("ap_password", password)
        logger.info("Generated a new pairing hotspot password for this device")

    return ssid, password


def is_own_ap(ssid):
    """True when `ssid` looks like this product's pairing hotspot.

    Used to keep the watchdog from mistaking our own hotspot for a network the
    owner asked us to join. The legacy fixed name is included so a device
    updated in the field does not strand itself on an old profile.
    """
    if not ssid:
        return False
    return ssid == f"{SSID_PREFIX}-Setup" or bool(
        re.fullmatch(rf"{SSID_PREFIX}-[0-9A-F]{{4}}", ssid))
