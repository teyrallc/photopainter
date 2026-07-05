"""
Authentication Blueprint for Vignette Smart Display.
Handles registration (1st user only), login, and hardware OTP verification.
"""
import hmac
import logging
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import (Blueprint, request, jsonify, session, render_template,
                   redirect, url_for)
from werkzeug.security import generate_password_hash, check_password_hash

from services import auth_mgr
from services import display_mgr
from services.auth_mgr import (generate_otp, verify_otp, rate_limited,
                               record_failure, record_success)

logger = logging.getLogger("vignette.auth")
bp = Blueprint('auth', __name__, url_prefix='/auth')

# A real bcrypt-style hash so failed logins still pay the hashing cost and
# cannot be distinguished by timing from a wrong-password-but-right-email login.
_DUMMY_HASH = generate_password_hash("vignette-dummy-password")

# Server-side pending-setup store (keyed by a random token kept in the session)
# so the password hash never rides in the client cookie.
_pending_setup = {}
_pending_lock = threading.Lock()
_PENDING_TTL = 900  # 15 minutes


def _form():
    """Accept both JSON and form posts (Flask 3 request.json raises on non-JSON)."""
    return request.get_json(silent=True) or request.form


def _too_many(action, **kw):
    blocked, retry = rate_limited(action, **kw)
    if blocked:
        return jsonify({"error": "Too many attempts. Try again later.",
                        "retry_after": retry}), 429
    return None


def _safe_next(target):
    """Only allow same-site relative redirects to avoid open-redirect abuse."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith('/') or target.startswith('//'):
        return None
    return target


@bp.route('/setup', methods=['GET', 'POST'])
def setup_admin():
    if auth_mgr.config.get("admin_email"):
        return redirect(url_for('auth.login'))

    if request.method == 'GET':
        return render_template('auth/setup.html')

    limited = _too_many("setup", max_fails=10, window=600, lockout=600)
    if limited:
        return limited

    data = _form()
    email = data.get('email', '').strip()
    password = data.get('password', '')

    if not email or not password or len(password) < 6:
        return jsonify({"error": "Invalid email or password (min 6 chars)."}), 400

    # Keep the hash server-side; hand the client only an opaque token.
    token = secrets.token_urlsafe(24)
    with _pending_lock:
        _pending_setup[token] = {
            'email': email,
            'password_hash': generate_password_hash(password),
            'ts': time.time(),
        }
    session['setup_token'] = token

    # Generate hardware OTP!
    generate_otp(email)

    return jsonify({"success": True, "needs_otp": True})


@bp.route('/setup-verify', methods=['POST'])
def setup_verify():
    if auth_mgr.config.get("admin_email"):
        return jsonify({"error": "Setup already complete."}), 403

    limited = _too_many("setup-verify", max_fails=8, window=600, lockout=900)
    if limited:
        return limited

    token = session.get('setup_token')
    with _pending_lock:
        pending = _pending_setup.get(token) if token else None
        if pending and time.time() - pending['ts'] > _PENDING_TTL:
            _pending_setup.pop(token, None)
            pending = None
    if not pending:
        return jsonify({"error": "No registration in progress."}), 400

    data = _form()
    code = data.get('code', '').strip()

    if not verify_otp(pending['email'], code):
        record_failure("setup-verify", max_fails=8, window=600, lockout=900)
        return jsonify({"error": "Invalid or expired OTP."}), 401

    # OTP verified! Now save the admin account
    auth_mgr.config.set("admin_email", pending['email'])
    auth_mgr.config.set("admin_password_hash", pending['password_hash'])

    with _pending_lock:
        _pending_setup.pop(token, None)
    session.pop('setup_token', None)
    session['logged_in'] = True
    session['email'] = pending['email']
    session.permanent = True
    record_success("setup-verify")

    # Mark setup as complete in config
    auth_mgr.config.set("setup_complete", True)

    # Restore display asynchronously
    try:
        thread = threading.Thread(target=display_mgr.display_current_page)
        thread.daemon = True
        thread.start()
        logger.info("Dashboard display thread started after setup.")
    except Exception as e:
        logger.error(f"Failed to start display thread: {e}")

    return jsonify({"success": True})


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('index'))

    if not auth_mgr.config.get("admin_email"):
        return redirect(url_for('auth.setup_admin'))

    if request.method == 'GET':
        return render_template('auth/login.html')

    limited = _too_many("login", max_fails=5, window=300, lockout=900)
    if limited:
        return limited

    data = _form()
    email = data.get('email', '').strip()
    password = data.get('password', '')

    saved_email = auth_mgr.config.get("admin_email") or ""
    saved_hash = auth_mgr.config.get("admin_password_hash") or ""

    # Always run a password hash (dummy when needed) so timing does not reveal
    # whether the email was the registered admin address.
    email_ok = hmac.compare_digest(email, saved_email)
    pw_ok = check_password_hash(saved_hash or _DUMMY_HASH, password)

    if not (email_ok and pw_ok):
        record_failure("login", max_fails=5, window=300, lockout=900)
        return jsonify({"error": "Invalid credentials."}), 401

    record_success("login")
    session['logged_in'] = True
    session['email'] = email
    session.permanent = True

    nxt = _safe_next(data.get('next') or request.args.get('next'))
    if request.is_json:
        return jsonify({"success": True, "redirect": nxt or url_for('index')})
    return redirect(nxt or url_for('index'))


@bp.route('/logout', methods=['POST'])
def logout():
    # POST-only so a cross-site GET/img cannot silently log the admin out.
    session.clear()
    if request.is_json:
        return jsonify({"success": True})
    return redirect(url_for('auth.login'))


@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'GET':
        return render_template('auth/forgot.html')

    limited = _too_many("forgot", max_fails=5, window=600, lockout=900)
    if limited:
        return limited

    data = _form()
    email = data.get('email', '').strip()

    # Anti-enumeration: respond identically whether or not the email matches.
    # Only actually issue an OTP for the registered admin address.
    admin_email = auth_mgr.config.get("admin_email") or ""
    if email and hmac.compare_digest(email, admin_email):
        generate_otp(email)
    else:
        record_failure("forgot", max_fails=5, window=600, lockout=900)

    if request.is_json:
        return jsonify({"success": True,
                        "message": "If that account exists, an OTP was shown "
                                   "on the e-paper display."})
    return redirect(url_for('auth.verify_reset', email=email))


@bp.route('/verify-reset', methods=['GET', 'POST'])
def verify_reset():
    if request.method == 'GET':
        email = request.args.get('email', '')
        return render_template('auth/verify.html', email=email)

    limited = _too_many("verify-reset", max_fails=8, window=600, lockout=900)
    if limited:
        return limited

    data = _form()
    email = data.get('email', '').strip()
    code = data.get('code', '').strip()
    new_password = data.get('new_password', '')

    # Only the registered admin can reset the admin password.
    admin_email = auth_mgr.config.get("admin_email") or ""
    if not admin_email or not hmac.compare_digest(email, admin_email):
        record_failure("verify-reset", max_fails=8, window=600, lockout=900)
        return jsonify({"error": "Invalid or expired OTP."}), 401

    # Validate the new password BEFORE consuming the one-time code, so a
    # client-side slip does not burn the OTP and force a full restart.
    if not new_password or len(new_password) < 6:
        return jsonify({"error": "Password too short (min 6 chars)."}), 400

    if not verify_otp(email, code):
        record_failure("verify-reset", max_fails=8, window=600, lockout=900)
        return jsonify({"error": "Invalid or expired OTP."}), 401

    record_success("verify-reset")
    auth_mgr.config.set("admin_password_hash", generate_password_hash(new_password))

    # Restore e-paper display asynchronously after the OTP was shown.
    try:
        thread = threading.Thread(target=display_mgr.display_current_page)
        thread.daemon = True
        thread.start()
        logger.info("Restore display thread started after password reset.")
    except Exception as e:
        logger.error(f"Failed to start restore display thread: {e}")

    if request.is_json:
        return jsonify({"success": True})
    return redirect(url_for('auth.login'))
