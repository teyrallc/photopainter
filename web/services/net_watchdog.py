"""Watch the WiFi link and fall back to the pairing hotspot when it is lost.

There are no physical buttons wired up, so when the saved network disappears —
the router was replaced, the password changed, the owner moved the frame to a
different house — the device had no way to say so and no way to be re-paired.
It sat on a stale screen looking broken.

After a minute with no connection it now raises its own hotspot and shows the
pairing screen, exactly as it does on a new device. This is **not** a reset:
the WiFi credentials, the admin account and the photo library are all left
alone, and while the fallback hotspot is up the device keeps quietly retrying
the saved network, so a router that simply took a while to reboot recovers on
its own with nobody touching anything.
"""

import logging
import threading
import time

from services import device_id

logger = logging.getLogger("vignette.netwatch")

# How long the link may be down before we conclude it is not coming straight
# back. The owner asked for a minute.
GRACE_SECONDS = 60

# How often to look while things are healthy.
POLL_SECONDS = 15

# While the fallback hotspot is up, how often to drop it and retry the saved
# network. Long enough not to interrupt somebody halfway through re-pairing.
RETRY_SECONDS = 300


class NetWatchdog:
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        # Injected by the app so this module does not import back into it.
        self._hooks = {}
        self._config = None
        self._fallback_active = False
        self._down_since = None

    def init(self, config, *, active_ssid, start_ap, stop_ap, scan_wifi,
             show_pairing_screen, connect_saved, is_pairing_in_progress):
        self._config = config
        self._hooks = {
            "active_ssid": active_ssid,
            "start_ap": start_ap,
            "stop_ap": stop_ap,
            "scan_wifi": scan_wifi,
            "show_pairing_screen": show_pairing_screen,
            "connect_saved": connect_saved,
            "is_pairing_in_progress": is_pairing_in_progress,
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="net-watchdog",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def fallback_active(self):
        return self._fallback_active

    # ── Internals ─────────────────────────────────────────────────────

    def _joined_ssid(self):
        """The network we are actually on, ignoring our own hotspot."""
        ssid = self._hooks["active_ssid"]()
        if device_id.is_own_ap(ssid):
            return None
        return ssid

    def _enter_fallback(self):
        logger.warning("Network unreachable — raising the pairing hotspot. "
                       "Saved credentials are kept.")
        self._fallback_active = True
        # setup_complete drives which page '/' serves and which routes are open
        # while unpaired. The credentials themselves stay in the config, so a
        # retry still has the password to try.
        self._config.set("setup_complete", False)
        try:
            self._hooks["scan_wifi"]()
            self._hooks["start_ap"]()
            self._hooks["show_pairing_screen"]()
        except Exception as e:
            logger.error(f"Failed to raise the pairing hotspot: {e}")

    def _leave_fallback(self, ssid):
        logger.info(f"Network recovered on {ssid!r} — taking the hotspot down")
        self._fallback_active = False
        self._down_since = None
        try:
            self._hooks["stop_ap"]()
        except Exception as e:
            logger.error(f"Failed to stop the fallback hotspot: {e}")
        self._config.set("setup_complete", True)

    def _retry_saved_network(self):
        """Drop the hotspot, try the saved network, put it back if that fails."""
        saved = self._config.get("wifi_ssid", "")
        if not saved:
            return False

        logger.info(f"Fallback retry: attempting {saved!r} again")
        try:
            self._hooks["stop_ap"]()
            time.sleep(3)
            if self._hooks["connect_saved"]():
                return True
        except Exception as e:
            logger.error(f"Fallback retry failed: {e}")

        # Still nothing. Put the pairing hotspot back so the owner keeps a way in.
        try:
            self._hooks["scan_wifi"]()
            self._hooks["start_ap"]()
        except Exception as e:
            logger.error(f"Could not restore the fallback hotspot: {e}")
        return False

    def _run(self):
        logger.info(f"Network watchdog started (grace {GRACE_SECONDS}s)")
        last_retry = time.time()

        while not self._stop.is_set():
            self._stop.wait(POLL_SECONDS)
            if self._stop.is_set():
                break

            try:
                # Never yank the interface out from under an in-progress join.
                if self._hooks["is_pairing_in_progress"]():
                    continue

                ssid = self._joined_ssid()

                if self._fallback_active:
                    if ssid:
                        self._leave_fallback(ssid)
                    elif time.time() - last_retry >= RETRY_SECONDS:
                        last_retry = time.time()
                        if self._retry_saved_network():
                            recovered = self._joined_ssid()
                            if recovered:
                                self._leave_fallback(recovered)
                    continue

                if ssid:
                    if self._down_since is not None:
                        logger.info(f"Network back on {ssid!r}")
                    self._down_since = None
                    continue

                # A device that has never been paired is already showing the
                # pairing screen; there is nothing here to rescue.
                if not self._config.get("wifi_ssid"):
                    continue

                if self._down_since is None:
                    self._down_since = time.time()
                    logger.warning("Network link lost — starting the grace period")
                    continue

                if time.time() - self._down_since >= GRACE_SECONDS:
                    self._enter_fallback()
                    last_retry = time.time()

            except Exception as e:
                logger.error(f"Network watchdog error: {e}", exc_info=True)

        logger.info("Network watchdog stopped")


service = NetWatchdog()
