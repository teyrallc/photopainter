import logging
import secrets
import time
from flask import session, redirect, url_for, request
from functools import wraps

logger = logging.getLogger("vignette.auth")

config = None

from services import display_mgr

# Caches for OTP mechanism
# Format: { 'email': {'code': '123456', 'expiry': timestamp, 'attempts': int} }
_otp_cache = {}

# A six-digit code sitting in a ten-minute window is only ~1.4 guesses per
# second away from being brute-forced, so the window alone is not the control —
# the attempt cap is.
MAX_OTP_ATTEMPTS = 5
OTP_TTL_SECONDS = 600


def set_config_ref(cfg):
    global config
    config = cfg


def generate_otp(email):
    # `random` is a Mersenne Twister: observing a couple of outputs is enough to
    # reconstruct its state and predict every code after them. Codes that gate
    # account registration and password reset need a CSPRNG.
    code = f"{secrets.randbelow(1000000):06d}"
    _otp_cache[email] = {
        'code': code,
        'expiry': time.time() + OTP_TTL_SECONDS,
        'attempts': 0,
    }

    # Display the OTP on the physical e-paper device asynchronously
    try:
        import threading
        thread = threading.Thread(target=display_mgr.display_otp_code, args=(code,))
        thread.daemon = True
        thread.start()
        logger.info(f"Hardware OTP display thread started for {email}")
    except Exception as e:
        logger.error(f"Failed to start OTP display thread: {e}")

    return code


def verify_otp(email, code):
    record = _otp_cache.get(email)
    if not record:
        return False
    if time.time() > record['expiry']:
        del _otp_cache[email]
        return False

    record['attempts'] += 1
    if record['attempts'] > MAX_OTP_ATTEMPTS:
        logger.warning(f"OTP for {email} burned after {MAX_OTP_ATTEMPTS} wrong attempts")
        del _otp_cache[email]
        return False

    # compare_digest keeps the check from leaking the code one character at a
    # time through response timing.
    if secrets.compare_digest(record['code'], str(code)):
        del _otp_cache[email]
        return True
    return False


def require_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # We only require login if an admin account actually exists!
        if not config.get("admin_email"):
            return redirect(url_for('auth.setup_admin'))
        if not session.get('logged_in'):
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function
