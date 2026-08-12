"""Remote access over an ngrok tunnel.

The device only listens on the LAN, so without a tunnel the console is
reachable only from the same network. A tunnel was already being opened, but
as two copies of the same fire-and-forget block: it ran once at start-up, and
once more after a successful WiFi join. If it failed — no network yet, ngrok's
agent still starting, the free tier's one-session limit tripped by a previous
process — nothing tried again, and the owner was quietly back to LAN-only
until the next reboot.

This is that logic as a supervised background service instead: it retries with
backoff, notices when a tunnel has died, reconnects, and tells the caller
whenever the public URL changes so the panel and the interface can follow it.
"""

import logging
import threading
import time
from urllib.parse import urlsplit

logger = logging.getLogger("vignette.remote")

# A tunnel that dies is usually a symptom of something slower (no DNS yet, the
# agent restarting), so back off rather than hammering ngrok's API — but keep
# the ceiling low enough that a device recovers on its own within a minute or
# two of the network coming back.
_RETRY_MIN = 5
_RETRY_MAX = 120
_HEALTH_INTERVAL = 30

_LOCAL_PORT = 5000


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
        self._disconnect()

    @property
    def url(self):
        with self._lock:
            return self._url

    @property
    def host(self):
        """Just the hostname, for comparing against a request's Host header."""
        url = self.url
        return urlsplit(url).netloc if url else None

    def state(self):
        with self._lock:
            return {
                "enabled": bool(self._config and
                                self._config.get("remote_access_enabled", True)),
                "status": self._status,
                "url": self._url,
                "error": self._error,
                "configured": bool(self._authtoken()),
            }

    # ── Internals ─────────────────────────────────────────────────────

    def _authtoken(self):
        if not self._config:
            return ""
        return (self._config.get("ngrok_authtoken") or "").strip()

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

    def _connect(self):
        from pyngrok import ngrok, conf

        token = self._authtoken()
        if not token:
            self._set("disabled", None, "No ngrok authtoken configured")
            return None

        conf.get_default().auth_token = token
        # A previous process (or a crashed one) can hold the free tier's single
        # agent session; clearing it first turns a permanent failure into a
        # restart.
        try:
            ngrok.kill()
        except Exception:
            pass

        tunnel = ngrok.connect(_LOCAL_PORT, "http")
        url = tunnel.public_url
        # ngrok hands back http:// on some plans even though the endpoint
        # terminates TLS; the browser should always be sent to the secure one.
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        return url

    def _disconnect(self):
        try:
            from pyngrok import ngrok
            ngrok.kill()
        except Exception:
            pass
        self._set("idle", None)

    def _tunnel_alive(self):
        try:
            from pyngrok import ngrok
            return bool(ngrok.get_tunnels())
        except Exception as e:
            logger.debug(f"Tunnel health check failed: {e}")
            return False

    def _run(self):
        logger.info("Remote access supervisor started")
        backoff = _RETRY_MIN

        while not self._stop.is_set():
            if not self._enabled():
                if self.url:
                    logger.info("Remote access disabled — closing the tunnel")
                    self._disconnect()
                self._set("disabled", None)
                self._stop.wait(_HEALTH_INTERVAL)
                continue

            if self.url and self._tunnel_alive():
                self._stop.wait(_HEALTH_INTERVAL)
                continue

            if self.url:
                logger.warning("Remote access tunnel dropped — reconnecting")
                self._set("connecting", None)

            try:
                self._set("connecting", None)
                url = self._connect()
                if url:
                    logger.info(f"Remote access online: {url}")
                    self._set("online", url)
                    backoff = _RETRY_MIN
                    self._stop.wait(_HEALTH_INTERVAL)
                    continue
                # Disabled for want of a token; _connect already said so.
                self._stop.wait(_HEALTH_INTERVAL)
                continue
            except Exception as e:
                logger.error(f"Remote access connect failed: {e}")
                self._set("error", None, str(e))

            self._stop.wait(backoff)
            backoff = min(_RETRY_MAX, backoff * 2)

        logger.info("Remote access supervisor stopped")


# One supervisor per process.
service = RemoteAccess()
