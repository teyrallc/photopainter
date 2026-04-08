import random
import logging
import time
from flask import session, redirect, url_for, request
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger("vignette.auth")

config = None

# We will import the renderer lazily to avoid circular imports if needed.
# from services import renderer

# Caches for OTP mechanism
_otp_cache = {}  # Format: { 'email': {'code': '123456', 'expiry': timestamp} }

def set_config_ref(cfg):
    global config
    config = cfg

def generate_otp(email):
    # Generate 6-digit code
    code = f"{random.randint(100000, 999999)}"
    _otp_cache[email] = {
        'code': code,
        'expiry': time.time() + 600 # 10 minutes
    }
    
    # Display the OTP on the physical e-paper device instead of sending email!
    try:
        from services import renderer
        from app import display_lock
        with display_lock:
            renderer.render_otp_code(code)
        logger.info(f"Hardware OTP displayed on E-Paper for {email}")
    except Exception as e:
        logger.error(f"Failed to display OTP on e-paper: {e}")
        
    return code

def verify_otp(email, code):
    record = _otp_cache.get(email)
    if not record:
        return False
    if time.time() > record['expiry']:
        del _otp_cache[email]
        return False
    if record['code'] == code:
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
