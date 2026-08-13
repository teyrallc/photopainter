"""Remote access — reaching the frame from outside the house.

The console only listens on the LAN, so without a tunnel it is unreachable the
moment you leave. Three providers, chosen by `remote_access_provider`:

  cloudflare  A named Cloudflare Tunnel with a fixed hostname. `cloudflared`
              runs as its own systemd service, so it starts at boot and
              survives restarts of this app; this module only reports the
              configured address and whether that service is up. The address
              never changes, which is the whole point.

  ngrok       Supervised in-process tunnel. Free ngrok issues a *new* hostname
              on every reconnect, so the panel has to be repainted each time
              and any address you wrote down goes stale. Fine for a prototype,
              wrong for something sitting on a wall.

  none        LAN only.

Whichever is chosen, the supervisor retries on failure and reports state; it
never lets a dead tunnel look like a working one.
"""

import logging
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit

logger = logging.getLogger("vignette.remote")

# A tunnel that dies is usually a symptom of something slower (no DNS yet, the
# agent restarting), so back off rather than hammering the provider — but keep
# the ceiling low enough that a device recovers on its own within a minute or
# two of the network coming back.
_RETRY_MIN = 5
_RETRY_MAX = 120
_HEALTH_INTERVAL = 30

_LOCAL_PORT = 5000

PROVIDERS = ("cloudflare", "ngrok", "none")
DEFAULT_PROVIDER = "ngrok"

# The systemd unit `cloudflared service install` creates.
_CLOUDFLARED_UNIT = "cloudflared"

# Prototype fallback for ngrok. The token used to be hard-coded at two call
# sites; it now lives in the config so each unit can carry its own, but a
# device that updates in the field has no value stored yet and would silently
# come back LAN-only. This keeps the internal fleet working across that update.
#
# BEFORE SHIPPING EXTERNALLY: revoke this token, delete this constant, and let
# `ngrok_authtoken` be the only source. Every unit sharing one ngrok account is
# a single point of failure and a single account to get banned.
_PROTOTYPE_AUTHTOKEN = "3By64a7MxTOJAWF3eD8TGoLcaIl_5tprPKw4ftjjRSTWU1eVM"


def normalize_url(url):
    """Coerce whatever the owner typed into a scheme-qualified origin.

    People paste `yilin.weienweng.com`, `www.example.com/`, and
    `http://host` interchangeably. A tunnel terminates TLS, so https is the
    right default, and a trailing slash would produce `//api/...` downstream.
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


class RemoteAccess:
    def __init__(self):
        self._config = None
        self._on_url_change = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._url = None
        self._status = "idle"       # idle | connecting | online | disabled | error
        self._error = None

    # ── Public surface ────────────────────────────────────────────────

    def init(self, config, on_url_change=None):
        self._config = config
        self._on_url_change = on_url_change

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="remote-access",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self.provider == "ngrok":
            self._kill_ngrok()
        self._set("idle", None)

    @property
    def provider(self):
        value = (self._config.get("remote_access_provider")
                 if self._config else None)
        return value if value in PROVIDERS else DEFAULT_PROVIDER

    @property
    def url(self):
        with self._lock:
            return self._url

    @property
    def host(self):
        """Just the hostname, for comparing against a request's Host header."""
        url = self.url
        return urlsplit(url).netloc if url else None

    @property
    def configured_host(self):
        """The hostname the owner configured, whether or not the tunnel is up.

        The same-origin check needs this even while the tunnel is down —
        otherwise a request that *did* arrive through it gets rejected during
        a reconnect.
        """
        url = normalize_url(self._config.get("remote_public_url")
                            if self._config else "")
        return urlsplit(url).netloc if url else None

    def state(self):
        with self._lock:
            return {
                "provider": self.provider,
                "enabled": bool(self._config and
                                self._config.get("remote_access_enabled", True)),
                "status": self._status,
                "url": self._url,
                "error": self._error,
                "configured": self._is_configured(),
                # Surfaced so the interface can say "using the shared prototype
                # account" rather than implying this device has its own.
                "shared_token": self.using_prototype_token(),
                # A fixed address does not go stale; a free ngrok one does.
                "stable_url": self.provider == "cloudflare",
            }

    def using_prototype_token(self):
        """True when ngrok is in use with no per-device token configured."""
        if self.provider != "ngrok":
            return False
        return not (self._config and (self._config.get("ngrok_authtoken") or "").strip())

    # ── Internals ─────────────────────────────────────────────────────

    def _is_configured(self):
        if self.provider == "cloudflare":
            return bool(self.configured_host)
        if self.provider == "ngrok":
            return bool(self._authtoken())
        return False

    def _authtoken(self):
        """The configured ngrok token, falling back to the prototype one."""
        if not self._config:
            return ""
        return (self._config.get("ngrok_authtoken") or "").strip() or _PROTOTYPE_AUTHTOKEN

    def _enabled(self):
        return bool(self._config and
                    self._config.get("remote_access_enabled", True))

    def _set(self, status, url=None, error=None):
        changed = False
        with self._lock:
            self._status = status
            self._error = error
            if url != self._url:
                self._url = url
                changed = True
        if changed and url and self._on_url_change:
            try:
                self._on_url_change(url)
            except Exception as e:
                logger.error(f"Remote access URL callback failed: {e}")

    # ── Cloudflare ────────────────────────────────────────────────────

    def _cloudflared_running(self):
        """Is the cloudflared service up?

        `systemctl is-active` is readable without privilege, so this works from
        the unprivileged service account.
        """
        if not shutil.which("systemctl"):
            return None          # not a systemd host; cannot tell
        try:
            result = subprocess.run(
                ["systemctl", "is-active", _CLOUDFLARED_UNIT],
                capture_output=True, text=True, timeout=10)
            return result.stdout.strip() == "active"
        except Exception as e:
            logger.debug(f"cloudflared status check failed: {e}")
            return None

    def _step_cloudflare(self):
        url = normalize_url(self._config.get("remote_public_url"))
        if not url:
            self._set("disabled", None,
                      "No public address configured for the Cloudflare tunnel")
            return

        running = self._cloudflared_running()
        if running is False:
            # The address is still the right one to show — the owner's DNS
            # points at it — but be honest that nothing is answering.
            self._set("error", url,
                      f"The {_CLOUDFLARED_UNIT} service is not running. "
                      f"Start it with: sudo systemctl start {_CLOUDFLARED_UNIT}")
            return

        # running is True, or None on a host where we cannot check.
        self._set("online", url)

    # ── ngrok ─────────────────────────────────────────────────────────

    def _kill_ngrok(self):
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except Exception:
            pass

    def _ngrok_connect(self):
        from pyngrok import ngrok, conf

        token = self._authtoken()
        if not token:
            self._set("disabled", None, "No ngrok authtoken configured")
            return None

        conf.get_default().auth_token = token
        # A previous process (or a crashed one) can hold the free tier's single
        # agent session; clearing it first turns a permanent failure into a
        # restart.
        self._kill_ngrok()

        tunnel = ngrok.connect(_LOCAL_PORT, "http")
        url = tunnel.public_url
        # ngrok hands back http:// on some plans even though the endpoint
        # terminates TLS; the browser should always be sent to the secure one.
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        return url

    def _ngrok_alive(self):
        try:
            from pyngrok import ngrok
            return bool(ngrok.get_tunnels())
        except Exception as e:
            logger.debug(f"Tunnel health check failed: {e}")
            return False

    def _step_ngrok(self, backoff):
        """One supervisor pass. Returns the next backoff to use."""
        if self.url and self._ngrok_alive():
            self._stop.wait(_HEALTH_INTERVAL)
            return _RETRY_MIN

        if self.url:
            logger.warning("Remote access tunnel dropped — reconnecting")

        try:
            self._set("connecting", None)
            url = self._ngrok_connect()
            if url:
                logger.info(f"Remote access online: {url}")
                self._set("online", url)
                self._stop.wait(_HEALTH_INTERVAL)
                return _RETRY_MIN
            # Disabled for want of a token; _ngrok_connect already said so.
            self._stop.wait(_HEALTH_INTERVAL)
            return backoff
        except Exception as e:
            logger.error(f"Remote access connect failed: {e}")
            self._set("error", None, str(e))

        self._stop.wait(backoff)
        return min(_RETRY_MAX, backoff * 2)

    # ── Supervisor ────────────────────────────────────────────────────

    def _run(self):
        logger.info(f"Remote access supervisor started (provider={self.provider})")
        backoff = _RETRY_MIN
        last_provider = self.provider

        while not self._stop.is_set():
            provider = self.provider

            # Switching provider in Settings must take effect without a reboot.
            if provider != last_provider:
                logger.info(f"Remote access provider changed: "
                            f"{last_provider} -> {provider}")
                if last_provider == "ngrok":
                    self._kill_ngrok()
                self._set("idle", None)
                last_provider = provider
                backoff = _RETRY_MIN

            if not self._enabled() or provider == "none":
                if self.url and last_provider == "ngrok":
                    self._kill_ngrok()
                self._set("disabled", None)
                self._stop.wait(_HEALTH_INTERVAL)
                continue

            if provider == "cloudflare":
                self._step_cloudflare()
                self._stop.wait(_HEALTH_INTERVAL)
                continue

            backoff = self._step_ngrok(backoff)

        logger.info("Remote access supervisor stopped")


# One supervisor per process.
service = RemoteAccess()
