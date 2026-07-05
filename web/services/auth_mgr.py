"""
Authentication support: hardware OTP generation/verification and a small
dependency-free in-memory rate limiter suitable for a single-device appliance.
"""
import hmac
import logging
import secrets
import threading
import time
from functools import wraps

from flask import session, redirect, url_for, request, jsonify

logger = logging.getLogger("vignette.auth")

config = None

from services import display_mgr

# OTP store: { 'email': {'code': '123456', 'expiry': ts, 'attempts': int} }
_otp_cache = {}
_otp_lock = threading.Lock()

OTP_TTL_SECONDS = 600          # 10 minutes
OTP_MAX_ATTEMPTS = 5           # invalidate the code after this many wrong tries


def set_config_ref(cfg):
    global config
    config = cfg


def generate_otp(email):
    # Cryptographically-secure 6-digit code (not the predictable Mersenne PRNG).
    code = f"{secrets.randbelow(1000000):06d}"
    with _otp_lock:
        _otp_cache[email] = {
            'code': code,
            'expiry': time.time() + OTP_TTL_SECONDS,
            'attempts': 0,
        }

    # Display the OTP on the physical e-paper device asynchronously
    try:
        thread = threading.Thread(target=display_mgr.display_otp_code, args=(code,))
        thread.daemon = True
        thread.start()
        logger.info(f"Hardware OTP display thread started for {email}")
    except Exception as e:
        logger.error(f"Failed to start OTP display thread: {e}")

    return code


def verify_otp(email, code):
    """Return True only for a valid, unexpired, non-exhausted code.

    Wrong guesses are counted; after OTP_MAX_ATTEMPTS the code is destroyed so a
    brute-force attacker cannot keep trying the 10^6 space within the TTL.
    """
    with _otp_lock:
        record = _otp_cache.get(email)
        if not record:
            return False
        if time.time() > record['expiry']:
            _otp_cache.pop(email, None)
            return False
        # Constant-time comparison to avoid leaking how many digits matched.
        if hmac.compare_digest(str(record['code']), str(code)):
            _otp_cache.pop(email, None)
            return True
        record['attempts'] += 1
        if record['attempts'] >= OTP_MAX_ATTEMPTS:
            logger.warning(f"OTP for {email} invalidated after too many attempts")
            _otp_cache.pop(email, None)
        return False


# ── In-memory rate limiter ────────────────────────────────────────────────
# Sliding-window failure counter with temporary lockout, keyed by an arbitrary
# string (typically "action:client_ip"). Good enough for a single-user frame.

_rl_lock = threading.Lock()
_rl_state = {}  # key -> {'fails': [ts, ...], 'locked_until': ts}


def _client_key(action):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown')
    ip = ip.split(',')[0].strip()
    return f"{action}:{ip}"


def rate_limited(action, max_fails=5, window=300, lockout=900):
    """Return (blocked: bool, retry_after: int) for the caller's key."""
    key = _client_key(action)
    now = time.time()
    with _rl_lock:
        st = _rl_state.get(key)
        if st and st.get('locked_until', 0) > now:
            return True, int(st['locked_until'] - now)
        if st:
            st['fails'] = [t for t in st['fails'] if t > now - window]
    return False, 0


def record_failure(action, max_fails=5, window=300, lockout=900):
    """Record a failed attempt; lock the key out once it exceeds max_fails."""
    key = _client_key(action)
    now = time.time()
    with _rl_lock:
        st = _rl_state.setdefault(key, {'fails': [], 'locked_until': 0})
        st['fails'] = [t for t in st['fails'] if t > now - window]
        st['fails'].append(now)
        if len(st['fails']) >= max_fails:
            st['locked_until'] = now + lockout
            st['fails'] = []
            logger.warning(f"Rate limit lockout for {key} ({lockout}s)")


def record_success(action):
    """Clear the failure record after a successful auth."""
    key = _client_key(action)
    with _rl_lock:
        _rl_state.pop(key, None)


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
