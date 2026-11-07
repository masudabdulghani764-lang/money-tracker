"""Application configuration with industry-standard security defaults."""
import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Database (SQLite – zero-config, stored locally)
DATABASE_PATH = os.path.join(BASE_DIR, "data", "money_tracker.db")

# Secret key persistence: write to a file if not in env so tokens remain valid across server restarts
SECRET_FILE = os.path.join(BASE_DIR, "data", ".secret_key")
os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
if not os.path.exists(SECRET_FILE):
    with open(SECRET_FILE, "w") as f:
        f.write(secrets.token_hex(32))

with open(SECRET_FILE, "r") as f:
    _persisted_secret = f.read().strip()

# JWT / Auth
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", _persisted_secret)
JWT_ACCESS_TOKEN_EXPIRES_SECONDS = 86400  # 24 hours
JWT_TOKEN_LOCATION = ["cookies"]
JWT_COOKIE_SECURE = False  # Works seamlessly across both HTTPS Cloudflare and local Wi-Fi
JWT_COOKIE_CSRF_PROTECT = True
JWT_COOKIE_SAMESITE = "Lax"

# SSL / TLS Certificates
SSL_DIR = os.path.join(BASE_DIR, "data", "ssl")
SSL_CERT_PATH = os.path.join(SSL_DIR, "cert.pem")
SSL_KEY_PATH = os.path.join(SSL_DIR, "key.pem")

# Security & Access Control
BCRYPT_LOG_ROUNDS = 12
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 5

# Multi-User Mode:
# When False, registration is open so other people can register and have their own isolated account.
PRIVATE_SINGLE_USER_MODE = False
SETUP_INVITE_KEY = os.environ.get("SETUP_INVITE_KEY", "")
