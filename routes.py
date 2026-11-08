"""Transaction, Category, Budget, Analytics & Recurring routes."""
import csv
import io
import re
from datetime import datetime, timedelta

from flask import Blueprint, Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from database import get_db

api_bp = Blueprint("api", __name__, url_prefix="/api")

HEX_COLOR_REGEX = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def sanitize_csv_cell(val):
    """Prevent CSV formula injection (DDE attacks)."""
    if val is None:
        return ""
    s = str(val)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


# ── Categories ───────────────────────────────────────────────────────────
@api_bp.route("/categories", methods=["GET"])
@jwt_required()
def list_categories():
    uid = get_jwt_identity()
    with get_db() as db:
        rows = db.execute("SELECT * FROM categories WHERE user_id = ? ORDER BY name", (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])


@api_bp.route("/categories", methods=["POST"])
@jwt_required()
def create_category():
    uid = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    icon = (data.get("icon") or "💰").strip()
    color = (data.get("color") or "#6366f1").strip()

    if not name:
        return jsonify({"errors": ["Nama kategori diperlukan."]}), 400
    if len(name) > 50:
        return jsonify({"errors": ["Nama kategori maksimal 50 karakter."]}), 400

    # Sanitize color
    if not HEX_COLOR_REGEX.match(color):
        color = "#6366f1"

    # Sanitize icon to prevent HTML injection
    if len(icon) > 10 or "<" in icon or ">" in icon:
        icon = "💰"

    try:
        with get_db() as db:
            cursor = db.execute(
                "INSERT INTO categories (user_id, name, icon, color) VALUES (?, ?, ?, ?)",
                (uid, name, icon, color),
            )
            cat_id = cursor.lastrowid
    except Exception:
        return jsonify({"errors": ["Kategori sudah ada."]}), 409

    return jsonify({"id": cat_id, "name": name, "icon": icon, "color": color}), 201


@api_bp.route("/categories/<int:cat_id>", methods=["DELETE"])
@jwt_required()
def delete_category(cat_id):
    uid = get_jwt_identity()
    with get_db() as db:
        db.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", (cat_id, uid))
    return jsonify({"message": "Kategori dihapus."}), 200


# ── Transactions ─────────────────────────────────────────────────────────
@api_bp.route("/transactions", methods=["GET"])
@jwt_required()
def list_transactions():
    uid = get_jwt_identity()
    month = request.args.get("month")
    tx_type = request.args.get("type")
    category_id = request.args.get("category_id")
    search = request.args.get("search", "").strip()
    limit = request.args.get("limit", 200, type=int)
    offset = request.args.get("offset", 0, type=int)

    query = """
        SELECT t.*, c.name as category_name, c.icon as category_icon, c.color as category_color
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = ?
    """
    params = [uid]

    if month:
        query += " AND t.date LIKE ?"
        params.append(f"{month}%")
    if tx_type:
        query += " AND t.type = ?"
        params.append(tx_type)
    if category_id:
        query += " AND t.category_id = ?"
        params.append(category_id)
    if search:
        query += " AND (t.description LIKE ? OR c.name LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    count_query = query.replace(
        "SELECT t.*, c.name as category_name, c.icon as category_icon, c.color as category_color",
        "SELECT COUNT(*) as cnt",
    )
    with get_db() as db:
        total = db.execute(count_query, params).fetchone()["cnt"]

    query += " ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db() as db:
        rows = db.execute(query, params).fetchall()

    return jsonify({"total": total, "transactions": [dict(r) for r in rows]})


@api_bp.route("/transactions", methods=["POST"])
@jwt_required()
def create_transaction():
    uid = get_jwt_identity()
    data = request.get_json(silent=True) or {}

    tx_type = data.get("type")
    amount = data.get("amount")
    description = (data.get("description") or "").strip()
    date = data.get("date")
    category_id = data.get("category_id") or None

    errors = []
    if tx_type not in ("income", "expense"):
        errors.append("Tipe harus 'income' atau 'expense'.")
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Jumlah harus angka positif.")

    if not date:
        errors.append("Tanggal diperlukan.")
    else:
        try:
            datetime.strptime(str(date)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            errors.append("Format tanggal tidak valid (harus YYYY-MM-DD).")

    if errors:
        return jsonify({"errors": errors}), 400

    with get_db() as db:
        # Validate category ownership to prevent IDOR
        if category_id:
            cat_check = db.execute(
                "SELECT id FROM categories WHERE id = ? AND user_id = ?",
                (category_id, uid),
            ).fetchone()
            if not cat_check:
                category_id = None

        cursor = db.execute(
            "INSERT INTO transactions (user_id, category_id, type, amount, description, date) VALUES (?,?,?,?,?,?)",
            (uid, category_id, tx_type, amount, description[:250], date[:10]),
        )
        tx_id = cursor.lastrowid

    return jsonify({"id": tx_id, "message": "Transaksi berhasil disimpan."}), 201


@api_bp.route("/transactions/<int:tx_id>", methods=["PUT"])
@jwt_required()
def update_transaction(tx_id):
    uid = get_jwt_identity()
    data = request.get_json(silent=True) or {}

    errors = []
    if "type" in data and data["type"] not in ("income", "expense"):
        errors.append("Tipe harus 'income' atau 'expense'.")
    if "amount" in data:
        try:
            amt = float(data["amount"])
            if amt <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Jumlah harus angka positif.")
    if "date" in data:
        try:
            datetime.strptime(str(data["date"])[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            errors.append("Format tanggal tidak valid (harus YYYY-MM-DD).")

    if errors:
        return jsonify({"errors": errors}), 400

    fields = []
    params = []
    with get_db() as db:
        for col in ("type", "amount", "description", "date", "category_id"):
            if col in data:
                fields.append(f"{col} = ?")
                val = data[col]
                if col == "category_id":
                    if val:
                        cat_check = db.execute(
                            "SELECT id FROM categories WHERE id = ? AND user_id = ?",
                            (val, uid),
                        ).fetchone()
                        val = cat_check["id"] if cat_check else None
                    else:
                        val = None
                elif col == "description" and val:
                    val = str(val)[:250]
                elif col == "date" and val:
                    val = str(val)[:10]
                params.append(val)

        if not fields:
            return jsonify({"errors": ["Tidak ada data untuk diupdate."]}), 400

        params.extend([tx_id, uid])
        db.execute(
            f"UPDATE transactions SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
            params,
        )
    return jsonify({"message": "Transaksi berhasil diubah."}), 200


@api_bp.route("/transactions/<int:tx_id>", methods=["DELETE"])
@jwt_required()
def delete_transaction(tx_id):
    uid = get_jwt_identity()
    with get_db() as db:
        db.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, uid))
    return jsonify({"message": "Transaksi berhasil dihapus."}), 200


# ── Export CSV ───────────────────────────────────────────────────────────
@api_bp.route("/transactions/export", methods=["GET"])
@jwt_required()
def export_transactions():
    uid = get_jwt_identity()
    month = request.args.get("month")
    if month and not re.match(r"^\d{4}-\d{2}$", month):
        month = None

    query = """
        SELECT t.date, t.type, c.name as category_name, t.description, t.amount
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = ?
    """
    params = [uid]
    if month:
        query += " AND t.date LIKE ?"
        params.append(f"{month}%")
    query += " ORDER BY t.date DESC, t.id DESC LIMIT 5000"

    with get_db() as db:
        rows = db.execute(query, params).fetchall()

    output = io.StringIO()
    # Add UTF-8 BOM so Excel opens indonesian characters cleanly
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(["Tanggal", "Tipe", "Kategori", "Deskripsi", "Jumlah (IDR)"])

    for r in rows:
        tipe_label = "Pemasukan" if r["type"] == "income" else "Pengeluaran"
        writer.writerow([
            sanitize_csv_cell(r["date"]),
            sanitize_csv_cell(tipe_label),
            sanitize_csv_cell(r["category_name"] or "-"),
            sanitize_csv_cell(r["description"] or "-"),
            r["amount"],
        ])

    safe_month_str = re.sub(r"[^0-9\-]", "", month) if month else "semua"
    filename = f"transaksi_{safe_month_str}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Summary / Dashboard ─────────────────────────────────────────────────
@api_bp.route("/summary", methods=["GET"])
@jwt_required()
def summary():
    uid = get_jwt_identity()
    month = request.args.get("month")

    date_filter = ""
    params_base = [uid]
    if month:
        date_filter = " AND date LIKE ?"
        params_base.append(f"{month}%")

    with get_db() as db:
        row = db.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN type='income'  THEN amount END), 0) AS total_income,
                COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) AS total_expense
            FROM transactions WHERE user_id = ? {date_filter}
            """,
            params_base,
        ).fetchone()

        total_income = row["total_income"]
        total_expense = row["total_expense"]
        balance = total_income - total_expense

        by_category = db.execute(
            f"""
            SELECT COALESCE(c.id, 0) AS id,
                   COALESCE(c.name, 'Tanpa Kategori') AS name,
                   COALESCE(c.icon, '📦') AS icon,
                   COALESCE(c.color, '#64748b') AS color,
                   t.type,
                   SUM(t.amount) AS total
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = ? {date_filter}
            GROUP BY COALESCE(c.id, 0), t.type
            ORDER BY total DESC
            """,
            params_base,
        ).fetchall()

        daily = db.execute(
            f"""
            SELECT date, type, SUM(amount) AS total
            FROM transactions
            WHERE user_id = ? {date_filter}
            GROUP BY date, type
            ORDER BY date
            """,
            params_base,
        ).fetchall()

        recent = db.execute(
            f"""
            SELECT t.*, c.name AS category_name, c.icon AS category_icon, c.color AS category_color
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = ? {date_filter}
            ORDER BY t.date DESC, t.id DESC
            LIMIT 8
            """,
            params_base,
        ).fetchall()

    return jsonify({
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance,
        "by_category": [dict(r) for r in by_category],
        "daily": [dict(r) for r in daily],
        "recent": [dict(r) for r in recent],
    })


# ── Financial Insights / Analytics ──────────────────────────────────────
@api_bp.route("/insights", methods=["GET"])
@jwt_required()
def insights():
    uid = get_jwt_identity()
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")

    # Current month data
    with get_db() as db:
        curr = db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN type='income' THEN amount END), 0) as income,
                COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) as expense,
                COUNT(*) as count
            FROM transactions
            WHERE user_id = ? AND date LIKE ?
            """,
            (uid, f"{month}%"),
        ).fetchone()

        # Previous month data for MoM comparison
        curr_dt = datetime.strptime(f"{month}-01", "%Y-%m-%d")
        prev_dt = (curr_dt.replace(day=1) - timedelta(days=1)).replace(day=1)
        prev_month = prev_dt.strftime("%Y-%m")

        prev = db.execute(
            """
            SELECT COALESCE(SUM(amount), 0) as expense
            FROM transactions
            WHERE user_id = ? AND date LIKE ? AND type='expense'
            """,
            (uid, f"{prev_month}%"),
        ).fetchone()

        # Top expense category
        top_cat = db.execute(
            """
            SELECT COALESCE(c.name, 'Tanpa Kategori') AS name,
                   COALESCE(c.icon, '📦') AS icon,
                   COALESCE(c.color, '#64748b') AS color,
                   SUM(t.amount) as total
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.user_id = ? AND t.date LIKE ? AND t.type='expense'
            GROUP BY COALESCE(c.id, 0)
            ORDER BY total DESC
            LIMIT 1
            """,
            (uid, f"{month}%"),
        ).fetchone()

        # Daily average
        distinct_days = db.execute(
            """
            SELECT COUNT(DISTINCT date) as days
            FROM transactions
            WHERE user_id = ? AND date LIKE ? AND type='expense'
            """,
            (uid, f"{month}%"),
        ).fetchone()["days"] or 1

    income = curr["income"]
    expense = curr["expense"]
    prev_expense = prev["expense"]

    savings_rate = 0.0
    if income > 0:
        savings_rate = round(((income - expense) / income) * 100, 1)

    mom_change = None
    if prev_expense > 0:
        mom_change = round(((expense - prev_expense) / prev_expense) * 100, 1)

    daily_avg = round(expense / max(1, distinct_days), 0)

    return jsonify({
        "month": month,
        "income": income,
        "expense": expense,
        "savings_rate": savings_rate,
        "daily_avg": daily_avg,
        "mom_change": mom_change,
        "transaction_count": curr["count"],
        "top_category": dict(top_cat) if top_cat else None,
    })


# ── Budgets ──────────────────────────────────────────────────────────────
@api_bp.route("/budgets", methods=["GET"])
@jwt_required()
def list_budgets():
    uid = get_jwt_identity()
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")

    with get_db() as db:
        rows = db.execute(
            """
            SELECT
                c.id as category_id,
                c.name as category_name,
                c.icon as category_icon,
                c.color as category_color,
                b.id as budget_id,
                COALESCE(b.monthly_limit, 0) as monthly_limit,
                COALESCE(spent_tbl.total_spent, 0) as spent
            FROM categories c
            LEFT JOIN budgets b ON b.category_id = c.id AND b.user_id = c.user_id
            LEFT JOIN (
                SELECT category_id, SUM(amount) as total_spent
                FROM transactions
                WHERE user_id = ? AND date LIKE ? AND type='expense'
                GROUP BY category_id
            ) spent_tbl ON spent_tbl.category_id = c.id
            WHERE c.user_id = ?
            ORDER BY b.monthly_limit DESC, c.name ASC
            """,
            (uid, f"{month}%", uid),
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        limit = d["monthly_limit"]
        spent = d["spent"]
        percentage = round((spent / limit * 100), 1) if limit > 0 else 0
        d["percentage"] = percentage
        d["remaining"] = max(0, limit - spent)
        result.append(d)

    return jsonify(result)


@api_bp.route("/budgets", methods=["POST"])
@jwt_required()
def set_budget():
    uid = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    category_id = data.get("category_id")
    monthly_limit = data.get("monthly_limit")

    try:
        monthly_limit = float(monthly_limit)
        if monthly_limit <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"errors": ["Batas budget bulanan harus berupa angka positif."]}), 400

    with get_db() as db:
        # Validate category ownership to prevent IDOR
        cat_check = db.execute(
            "SELECT id FROM categories WHERE id = ? AND user_id = ?",
            (category_id, uid),
        ).fetchone()
        if not cat_check:
            return jsonify({"errors": ["Kategori tidak valid atau tidak ditemukan."]}), 400

        db.execute(
            """
            INSERT INTO budgets (user_id, category_id, monthly_limit)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, category_id) DO UPDATE SET monthly_limit=excluded.monthly_limit
            """,
            (uid, category_id, monthly_limit),
        )
    return jsonify({"message": "Budget kategori berhasil disimpan."}), 200


@api_bp.route("/budgets/<int:cat_id>", methods=["DELETE"])
@jwt_required()
def delete_budget(cat_id):
    uid = get_jwt_identity()
    with get_db() as db:
        db.execute("DELETE FROM budgets WHERE user_id = ? AND category_id = ?", (uid, cat_id))
    return jsonify({"message": "Target budget dihapus."}), 200


# ── Recurring Subscriptions ──────────────────────────────────────────────
@api_bp.route("/recurring", methods=["GET"])
@jwt_required()
def list_recurring():
    uid = get_jwt_identity()
    with get_db() as db:
        rows = db.execute(
            """
            SELECT r.*, c.name as category_name, c.icon as category_icon, c.color as category_color
            FROM recurring_transactions r
            LEFT JOIN categories c ON r.category_id = c.id
            WHERE r.user_id = ?
            ORDER BY r.due_day ASC
            """,
            (uid,),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@api_bp.route("/recurring", methods=["POST"])
@jwt_required()
def create_recurring():
    uid = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    tx_type = data.get("type", "expense")
    amount = data.get("amount")
    due_day = data.get("due_day", 1)
    category_id = data.get("category_id") or None

    if not name:
        return jsonify({"errors": ["Nama langganan/tagihan diperlukan."]}), 400
    if len(name) > 60:
        return jsonify({"errors": ["Nama langganan maksimal 60 karakter."]}), 400

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"errors": ["Jumlah nominal harus positif."]}), 400

    try:
        due_day = int(due_day)
        if due_day < 1 or due_day > 31:
            due_day = 1
    except (TypeError, ValueError):
        due_day = 1

    with get_db() as db:
        # Validate category ownership to prevent IDOR
        if category_id:
            cat_check = db.execute(
                "SELECT id FROM categories WHERE id = ? AND user_id = ?",
                (category_id, uid),
            ).fetchone()
            if not cat_check:
                category_id = None

        cursor = db.execute(
            """
            INSERT INTO recurring_transactions (user_id, category_id, name, type, amount, due_day)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uid, category_id, name, tx_type, amount, due_day),
        )
        rec_id = cursor.lastrowid

    return jsonify({"id": rec_id, "message": "Langganan rutin berhasil dicatat."}), 201


@api_bp.route("/recurring/<int:rec_id>", methods=["DELETE"])
@jwt_required()
def delete_recurring(rec_id):
    uid = get_jwt_identity()
    with get_db() as db:
        db.execute("DELETE FROM recurring_transactions WHERE id = ? AND user_id = ?", (rec_id, uid))
    return jsonify({"message": "Langganan rutin dihapus."}), 200


@api_bp.route("/recurring/<int:rec_id>/record", methods=["POST"])
@jwt_required()
def record_recurring_now(rec_id):
    """1-click record of a recurring item into transactions today."""
    uid = get_jwt_identity()
    today_str = datetime.now().strftime("%Y-%m-%d")

    with get_db() as db:
        rec = db.execute(
            "SELECT * FROM recurring_transactions WHERE id = ? AND user_id = ?",
            (rec_id, uid),
        ).fetchone()

        if not rec:
            return jsonify({"errors": ["Data langganan tidak ditemukan."]}), 404

        cursor = db.execute(
            "INSERT INTO transactions (user_id, category_id, type, amount, description, date) VALUES (?,?,?,?,?,?)",
            (uid, rec["category_id"], rec["type"], rec["amount"], f"[Rutin] {rec['name']}", today_str),
        )
        tx_id = cursor.lastrowid

    return jsonify({"id": tx_id, "message": f"Transaksi '{rec['name']}' berhasil dicatat ke hari ini!"}), 201
