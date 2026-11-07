"""Industry-Standard Authentication & Security Blueprint.
Includes:
 - Bcrypt 12-round hashing
 - Single-Owner Exclusive Lock
 - Two-Factor Authentication (TOTP - RFC 6238)
 - Rate Limiting & Anti-Brute-Force
 - JWT HTTP-Only Cookies + CSRF Protection
 - Security Audit Logging
"""
import time
from datetime import timedelta

import bcrypt
import pyotp
from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    decode_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    unset_jwt_cookies,
)

import config
from database import get_db, log_audit, seed_categories

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(config.BCRYPT_LOG_ROUNDS)).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def get_client_ip() -> str:
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def is_rate_limited(identifier: str, ip: str) -> bool:
    """Check if identifier or IP exceeded failed login attempts in LOCKOUT_MINUTES."""
    cutoff = time.time() - (config.LOCKOUT_MINUTES * 60)
    with get_db() as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS failed_count
            FROM login_attempts
            WHERE (identifier = ? OR ip_address = ?)
              AND attempt_time > ?
              AND success = 0
            """,
            (identifier, ip, cutoff),
        ).fetchone()
        return (row["failed_count"] or 0) >= config.MAX_LOGIN_ATTEMPTS


def record_login_attempt(identifier: str, ip: str, success: bool):
    with get_db() as db:
        db.execute(
            "INSERT INTO login_attempts (identifier, ip_address, attempt_time, success) VALUES (?, ?, ?, ?)",
            (identifier, ip, time.time(), 1 if success else 0),
        )


@auth_bp.route("/status", methods=["GET"])
def system_status():
    """Return whether the system allows registration (single owner guard)."""
    with get_db() as db:
        user_count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    can_register = True
    if config.PRIVATE_SINGLE_USER_MODE and user_count >= 1:
        can_register = False

    return jsonify({
        "can_register": can_register,
        "user_count": user_count,
        "single_user_mode": config.PRIVATE_SINGLE_USER_MODE,
        "security_features": {
            "two_factor_auth": True,
            "rate_limiting": True,
            "csrf_protection": True,
            "bcrypt_rounds": config.BCRYPT_LOG_ROUNDS,
            "single_owner_exclusive": config.PRIVATE_SINGLE_USER_MODE,
        },
    })


@auth_bp.route("/register", methods=["POST"])
def register():
    ip = get_client_ip()
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    invite_key = (data.get("invite_key") or "").strip()

    with get_db() as db:
        user_count = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    # Enforce Single-Owner Exclusive Lock
    if config.PRIVATE_SINGLE_USER_MODE and user_count >= 1:
        if not config.SETUP_INVITE_KEY or invite_key != config.SETUP_INVITE_KEY:
            return jsonify({
                "errors": [
                    "Akses registrasi ditutup. Sistem ini dalam mode privat dan hanya dapat diakses oleh pemilik yang sah."
                ]
            }), 403

    errors = []
    if len(username) < 3 or len(username) > 30:
        errors.append("Username harus 3 - 30 karakter.")
    if "@" not in email or "." not in email:
        errors.append("Alamat email tidak valid.")
    if len(password) < 8:
        errors.append("Password minimal 8 karakter.")
    elif not any(c.isupper() or c.isdigit() for c in password):
        errors.append("Password harus mengandung huruf besar atau angka untuk standar keamanan.")

    if errors:
        return jsonify({"errors": errors}), 400

    hashed = hash_password(password)
    role = "owner" if user_count == 0 else "user"

    try:
        with get_db() as db:
            cursor = db.execute(
                "INSERT INTO users (username, email, password, role) VALUES (?, ?, ?, ?)",
                (username, email, hashed, role),
            )
            user_id = cursor.lastrowid
            seed_categories(user_id, db=db)
            log_audit(user_id, f"Registrasi akun ({role}) berhasil", ip, request.user_agent.string, db=db)
    except Exception:
        return jsonify({"errors": ["Username atau email sudah terdaftar."]}), 409

    token = create_access_token(
        identity=str(user_id),
        additional_claims={"role": role, "username": username},
        expires_delta=timedelta(seconds=config.JWT_ACCESS_TOKEN_EXPIRES_SECONDS),
    )
    resp = jsonify({
        "message": "Registrasi berhasil! Akun pemilik telah dibuat.",
        "user": {"id": user_id, "username": username, "email": email, "role": role, "totp_enabled": False},
    })
    set_access_cookies(resp, token)
    return resp, 201


@auth_bp.route("/login", methods=["POST"])
def login():
    ip = get_client_ip()
    data = request.get_json(silent=True) or {}
    login_id = (data.get("login") or "").strip()
    password = data.get("password") or ""

    if not login_id or not password:
        return jsonify({"errors": ["Username/email dan password wajib diisi."]}), 400

    # Rate limiting protection
    if is_rate_limited(login_id.lower(), ip):
        return jsonify({
            "errors": [
                f"Terlalu banyak percobaan gagal. Akses dibatasi selama {config.LOCKOUT_MINUTES} menit untuk keamanan."
            ]
        }), 429

    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (login_id, login_id.lower()),
        ).fetchone()

    if not user or not check_password(password, user["password"]):
        record_login_attempt(login_id.lower(), ip, success=False)
        return jsonify({"errors": ["Kredensial tidak valid."]}), 401

    # Record successful password step
    record_login_attempt(login_id.lower(), ip, success=True)

    # Check 2FA requirement
    if user["totp_enabled"]:
        # Issue short-lived temporary token for 2FA verification step (5 mins)
        temp_token = create_access_token(
            identity=str(user["id"]),
            additional_claims={"2fa_pending": True},
            expires_delta=timedelta(minutes=5),
        )
        return jsonify({
            "status": "2fa_required",
            "message": "Verifikasi dua langkah (2FA) diperlukan.",
            "temp_token": temp_token,
        }), 200

    # Full login success
    token = create_access_token(
        identity=str(user["id"]),
        additional_claims={"role": user["role"], "username": user["username"]},
        expires_delta=timedelta(seconds=config.JWT_ACCESS_TOKEN_EXPIRES_SECONDS),
    )
    log_audit(user["id"], "Login berhasil", ip, request.user_agent.string)

    resp = jsonify({
        "message": "Login berhasil!",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "totp_enabled": bool(user["totp_enabled"]),
        },
    })
    set_access_cookies(resp, token)
    return resp, 200


@auth_bp.route("/verify-2fa", methods=["POST"])
def verify_2fa():
    """Verify 6-digit TOTP code during login."""
    ip = get_client_ip()
    data = request.get_json(silent=True) or {}
    temp_token = data.get("temp_token") or ""
    code = (data.get("code") or "").strip().replace(" ", "")

    if not temp_token or not code:
        return jsonify({"errors": ["Token dan kode verifikasi 6 digit diperlukan."]}), 400

    try:
        decoded = decode_token(temp_token)
        if not decoded.get("2fa_pending"):
            return jsonify({"errors": ["Token verifikasi tidak valid."]}), 401
        user_id = decoded["sub"]
    except Exception:
        return jsonify({"errors": ["Sesi verifikasi kadaluarsa. Silakan login ulang."]}), 401

    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    if not user or not user["totp_secret"]:
        return jsonify({"errors": ["Konfigurasi 2FA tidak ditemukan."]}), 400

    totp = pyotp.TOTP(user["totp_secret"])
    if not totp.verify(code, valid_window=1):
        return jsonify({"errors": ["Kode 2FA tidak valid atau sudah kadaluarsa."]}), 401

    # 2FA passed! Issue full access token
    token = create_access_token(
        identity=str(user["id"]),
        additional_claims={"role": user["role"], "username": user["username"]},
        expires_delta=timedelta(seconds=config.JWT_ACCESS_TOKEN_EXPIRES_SECONDS),
    )
    log_audit(user["id"], "Login berhasil (terverifikasi 2FA)", ip, request.user_agent.string)

    resp = jsonify({
        "message": "Autentikasi 2FA berhasil!",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "totp_enabled": True,
        },
    })
    set_access_cookies(resp, token)
    return resp, 200


@auth_bp.route("/2fa/setup", methods=["GET"])
@jwt_required()
def setup_2fa():
    """Generate a new TOTP secret and provisioning URI."""
    user_id = get_jwt_identity()
    with get_db() as db:
        user = db.execute("SELECT email, username, totp_enabled FROM users WHERE id = ?", (user_id,)).fetchone()

    if not user:
        return jsonify({"errors": ["User tidak ditemukan."]}), 404

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=user["email"],
        issuer_name="Money Tracker by Mas'ud",
    )

    return jsonify({
        "secret": secret,
        "otpauth_url": provisioning_uri,
        "is_enabled": bool(user["totp_enabled"]),
    })


@auth_bp.route("/2fa/enable", methods=["POST"])
@jwt_required()
def enable_2fa():
    """Confirm and activate 2FA with secret and current code."""
    ip = get_client_ip()
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    secret = (data.get("secret") or "").strip()
    code = (data.get("code") or "").strip().replace(" ", "")

    if not secret or not code:
        return jsonify({"errors": ["Secret dan kode 6 digit diperlukan."]}), 400

    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        return jsonify({"errors": ["Kode OTP tidak sesuai. Pastikan jam pada perangkat Anda akurat."]}), 400

    with get_db() as db:
        db.execute(
            "UPDATE users SET totp_secret = ?, totp_enabled = 1 WHERE id = ?",
            (secret, user_id),
        )
        log_audit(user_id, "2FA TOTP diaktifkan", ip, request.user_agent.string, db=db)

    return jsonify({"message": "Autentikasi Dua Faktor (2FA) berhasil diaktifkan!"}), 200


@auth_bp.route("/2fa/disable", methods=["POST"])
@jwt_required()
def disable_2fa():
    """Disable 2FA with password confirmation."""
    ip = get_client_ip()
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""

    if not password:
        return jsonify({"errors": ["Konfirmasi password diperlukan untuk menonaktifkan 2FA."]}), 400

    with get_db() as db:
        user = db.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or not check_password(password, user["password"]):
            return jsonify({"errors": ["Password salah."]}), 401

        db.execute("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?", (user_id,))
        log_audit(user_id, "2FA TOTP dinonaktifkan", ip, request.user_agent.string, db=db)

    return jsonify({"message": "2FA telah dinonaktifkan."}), 200


@auth_bp.route("/change-password", methods=["POST"])
@jwt_required()
def change_password():
    """Industry-standard password change flow."""
    ip = get_client_ip()
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""

    if not old_password or not new_password:
        return jsonify({"errors": ["Password lama dan baru wajib diisi."]}), 400

    if len(new_password) < 8 or not any(c.isupper() or c.isdigit() for c in new_password):
        return jsonify({"errors": ["Password baru minimal 8 karakter dan memiliki huruf besar/angka."]}), 400

    with get_db() as db:
        user = db.execute("SELECT password FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or not check_password(old_password, user["password"]):
            return jsonify({"errors": ["Password lama salah."]}), 401

        hashed = hash_password(new_password)
        db.execute("UPDATE users SET password = ? WHERE id = ?", (hashed, user_id))
        log_audit(user_id, "Password akun berhasil diubah", ip, request.user_agent.string, db=db)

    return jsonify({"message": "Password berhasil diperbarui!"}), 200


@auth_bp.route("/audit-logs", methods=["GET"])
@jwt_required()
def audit_logs():
    """Retrieve security audit trail for user."""
    user_id = get_jwt_identity()
    with get_db() as db:
        rows = db.execute(
            "SELECT action, ip_address, created_at FROM audit_logs WHERE user_id = ? ORDER BY id DESC LIMIT 15",
            (user_id,),
        ).fetchall()
    return jsonify([dict(r) for r in rows]), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    resp = jsonify({"message": "Logout berhasil."})
    unset_jwt_cookies(resp)
    return resp, 200


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user_id = get_jwt_identity()
    with get_db() as db:
        user = db.execute(
            "SELECT id, username, email, role, totp_enabled, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not user:
        return jsonify({"errors": ["User tidak ditemukan."]}), 404
    d = dict(user)
    d["totp_enabled"] = bool(d["totp_enabled"])
    return jsonify(d), 200
