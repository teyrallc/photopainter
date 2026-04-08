"""
Authentication Blueprint for Vignette Smart Display.
Handles registration (1st user only), login, and hardware OTP verification.
"""
import logging
from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
from services import auth_mgr
from services.auth_mgr import generate_otp, verify_otp
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger("vignette.auth")
bp = Blueprint('auth', __name__, url_prefix='/auth')

@bp.route('/setup', methods=['GET', 'POST'])
def setup_admin():
    if auth_mgr.config.get("admin_email"):
        return redirect(url_for('auth.login'))

    if request.method == 'GET':
        return render_template('auth/setup.html')

    # POST
    data = request.json or request.form
    email = data.get('email', '').strip()
    password = data.get('password', '')

    if not email or not password or len(password) < 6:
        return jsonify({"error": "Invalid email or password (min 6 chars)."}), 400

    # Store pending registration in session
    session['pending_setup'] = {
        'email': email,
        'password_hash': generate_password_hash(password)
    }
    
    # Generate hardware OTP!
    generate_otp(email)

    return jsonify({"success": True, "needs_otp": True})


@bp.route('/setup-verify', methods=['POST'])
def setup_verify():
    if auth_mgr.config.get("admin_email"):
        return jsonify({"error": "Setup already complete."}), 403

    pending = session.get('pending_setup')
    if not pending:
        return jsonify({"error": "No registration in progress."}), 400

    data = request.json or request.form
    code = data.get('code', '').strip()

    if not verify_otp(pending['email'], code):
        return jsonify({"error": "Invalid or expired OTP."}), 401

    # OTP verified! Now save the admin account
    auth_mgr.config.set("admin_email", pending['email'])
    auth_mgr.config.set("admin_password_hash", pending['password_hash'])
    
    # Clear pending data and log in
    session.pop('pending_setup', None)
    session['logged_in'] = True
    session['email'] = pending['email']
    
    # Mark setup as complete in config
    auth_mgr.config.set("setup_complete", True)
    
    from app import display_current_page
    try:
        display_current_page()
    except Exception as e:
        logger.error(f"Failed to restore display: {e}")

    return jsonify({"success": True})


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('index'))

    if not auth_mgr.config.get("admin_email"):
        return redirect(url_for('auth.setup_admin'))

    if request.method == 'GET':
        return render_template('auth/login.html')

    # POST
    data = request.json or request.form
    email = data.get('email', '').strip()
    password = data.get('password', '')

    saved_email = auth_mgr.config.get("admin_email")
    saved_hash = auth_mgr.config.get("admin_password_hash")

    if email != saved_email or not check_password_hash(saved_hash, password):
        return jsonify({"error": "Invalid credentials."}), 401

    session['logged_in'] = True
    session['email'] = email

    if request.is_json:
        return jsonify({"success": True})
    return redirect(url_for('index'))


@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'GET':
        return render_template('auth/forgot.html')

    data = request.json or request.form
    email = data.get('email', '').strip()

    if email != auth_mgr.config.get("admin_email"):
        return jsonify({"error": "Unrecognized email."}), 404

    # Generate hardware OTP!
    generate_otp(email)

    if request.is_json:
        return jsonify({"success": True, "message": "OTP displayed on e-paper."})
    return redirect(url_for('auth.verify_reset', email=email))


@bp.route('/verify-reset', methods=['GET', 'POST'])
def verify_reset():
    if request.method == 'GET':
        email = request.args.get('email', '')
        return render_template('auth/verify.html', email=email)

    data = request.json or request.form
    email = data.get('email', '').strip()
    code = data.get('code', '').strip()
    new_password = data.get('new_password', '')

    if not verify_otp(email, code):
        return jsonify({"error": "Invalid or expired OTP."}), 401

    if not new_password or len(new_password) < 6:
        return jsonify({"error": "Password too short."}), 400

    auth_mgr.config.set("admin_password_hash", generate_password_hash(new_password))
    
    # Restore e-paper display after OTP was shown
    try:
        from app import display_current_page
        display_current_page()
    except Exception as e:
        logger.error(f"Failed to restore display: {e}")

    if request.is_json:
        return jsonify({"success": True})
    return redirect(url_for('auth.login'))
