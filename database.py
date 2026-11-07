"""Database helper – SQLite with schema auto-migration and industry security tables."""
import os
import sqlite3
from contextlib import contextmanager

import config

_DB_DIR = os.path.dirname(config.DATABASE_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT    NOT NULL UNIQUE,
    email        TEXT    NOT NULL UNIQUE,
    password     TEXT    NOT NULL,
    role         TEXT    NOT NULL DEFAULT 'owner',
    totp_secret  TEXT    DEFAULT NULL,
    totp_enabled INTEGER DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    icon        TEXT    DEFAULT '💰',
    color       TEXT    DEFAULT '#6366f1',
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    category_id INTEGER,
    type        TEXT    NOT NULL CHECK(type IN ('income', 'expense')),
    amount      REAL    NOT NULL CHECK(amount > 0),
    description TEXT    DEFAULT '',
    date        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id)     REFERENCES users(id)      ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS budgets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    category_id   INTEGER NOT NULL,
    monthly_limit REAL    NOT NULL CHECK(monthly_limit > 0),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id)     REFERENCES users(id)      ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE,
    UNIQUE(user_id, category_id)
);

CREATE TABLE IF NOT EXISTS recurring_transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    category_id INTEGER,
    name        TEXT    NOT NULL,
    type        TEXT    NOT NULL CHECK(type IN ('income', 'expense')),
    amount      REAL    NOT NULL CHECK(amount > 0),
    frequency   TEXT    NOT NULL DEFAULT 'monthly',
    due_day     INTEGER DEFAULT 1,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id)     REFERENCES users(id)      ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT    NOT NULL,
    ip_address TEXT    DEFAULT '',
    user_agent TEXT    DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier  TEXT NOT NULL,
    ip_address  TEXT NOT NULL,
    attempt_time REAL NOT NULL,
    success     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_transactions_user   ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date   ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_categories_user     ON categories(user_id);
CREATE INDEX IF NOT EXISTS idx_budgets_user        ON budgets(user_id);
CREATE INDEX IF NOT EXISTS idx_recurring_user      ON recurring_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_user          ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_login_attempts_key  ON login_attempts(identifier, attempt_time);
"""

DEFAULT_CATEGORIES = [
    ("Makanan & Minuman",  "🍔", "#ef4444"),
    ("Transportasi",       "🚗", "#f59e0b"),
    ("Belanja",            "🛒", "#10b981"),
    ("Hiburan",            "🎬", "#8b5cf6"),
    ("Tagihan & Utilitas", "💡", "#3b82f6"),
    ("Kesehatan",          "🏥", "#ec4899"),
    ("Pendidikan",         "📚", "#14b8a6"),
    ("Gaji",               "💼", "#22c55e"),
    ("Investasi",          "📈", "#6366f1"),
    ("Lainnya",            "📦", "#64748b"),
]


def init_db():
    """Create tables and migrate schema if needed."""
    os.makedirs(_DB_DIR, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA)

        # Migration columns if existing db
        cursor = db.execute("PRAGMA table_info(users)")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        if "role" not in existing_cols:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'owner'")
        if "totp_secret" not in existing_cols:
            db.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT DEFAULT NULL")
        if "totp_enabled" not in existing_cols:
            db.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0")


@contextmanager
def get_db():
    """Yield a database connection with row_factory."""
    conn = sqlite3.connect(config.DATABASE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_categories(user_id: int, db=None):
    """Insert default categories for a new user."""
    items = [(user_id, n, i, c) for n, i, c in DEFAULT_CATEGORIES]
    sql = "INSERT OR IGNORE INTO categories (user_id, name, icon, color) VALUES (?, ?, ?, ?)"
    if db is not None:
        db.executemany(sql, items)
    else:
        with get_db() as conn:
            conn.executemany(sql, items)


def log_audit(user_id: int, action: str, ip: str = "", user_agent: str = "", db=None):
    """Record an audit trail event."""
    try:
        sql = "INSERT INTO audit_logs (user_id, action, ip_address, user_agent) VALUES (?, ?, ?, ?)"
        args = (user_id, action, ip[:100], user_agent[:250])
        if db is not None:
            db.execute(sql, args)
        else:
            with get_db() as conn:
                conn.execute(sql, args)
    except Exception:
        pass
