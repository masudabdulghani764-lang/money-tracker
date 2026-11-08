"""Money Tracker – Flask Application Entry Point with Industry Security Headers."""
import os
from datetime import timedelta

from flask import Flask, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager

import config
from auth import auth_bp
from database import init_db
from routes import api_bp


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="")

    # ── Config ────────────────────────────────────────────────────────
    app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(seconds=config.JWT_ACCESS_TOKEN_EXPIRES_SECONDS)
    app.config["JWT_TOKEN_LOCATION"] = config.JWT_TOKEN_LOCATION
    app.config["JWT_COOKIE_SECURE"] = config.JWT_COOKIE_SECURE
    app.config["JWT_COOKIE_CSRF_PROTECT"] = config.JWT_COOKIE_CSRF_PROTECT
    app.config["JWT_COOKIE_SAMESITE"] = config.JWT_COOKIE_SAMESITE

    # ── Extensions ────────────────────────────────────────────────────
    allowed_origins = [
        "https://masudghani.pythonanywhere.com",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://localhost:3000",
    ]
    CORS(app, origins=allowed_origins, supports_credentials=True)
    jwt = JWTManager(app)

    # ── Security Headers (Industry Standard) ──────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data: https: blob:; "
            "connect-src 'self';"
        )
        return response

    # ── Blueprints ────────────────────────────────────────────────────
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)

    # ── Serve Frontend SPA ────────────────────────────────────────────
    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.errorhandler(404)
    def not_found(e):
        return send_from_directory(app.static_folder, "index.html")

    # ── Init DB ───────────────────────────────────────────────────────
    with app.app_context():
        init_db()

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  💎 Money Tracker by Mas'ud running at:")
    print(f"     - Lokal (Laptop): http://localhost:{port}")
    print(f"     - Jaringan (HP/Tablet): http://192.168.1.2:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
