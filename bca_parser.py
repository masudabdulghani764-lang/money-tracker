"""BCA Email & Statement Smart Parser Engine.
Extracts transaction date, amount, type, description, and auto-matches categories
from BCA email notifications (m-BCA, myBCA, KlikBCA, QRIS, Debit, and e-Statements).
"""
import imaplib
import re
from datetime import datetime
from email import policy
from email.parser import BytesParser

MONTH_MAP = {
    "jan": "01", "januari": "01",
    "feb": "02", "februari": "02",
    "mar": "03", "maret": "03",
    "apr": "04", "april": "04",
    "mei": "05", "may": "05",
    "jun": "06", "juni": "06",
    "jul": "07", "juli": "07",
    "agu": "08", "agustus": "08", "ags": "08",
    "sep": "09", "september": "09",
    "okt": "10", "oktober": "10",
    "nov": "11", "november": "11",
    "des": "12", "desember": "12",
}

CATEGORY_KEYWORDS = {
    "Makanan & Minuman": [
        "makan", "resto", "restoran", "kopi", "coffee", "cafe", "kafe", "warung",
        "bakso", "ayam", "burger", "pizza", "mcd", "mcdonald", "kfc", "starbucks",
        "hokben", "gofood", "grabfood", "shopeefood", "dapur", "rotio", "chatime",
        "mixue", "food", "kuliner", "mie", "sate", "nasi", "martabak"
    ],
    "Tagihan & Utilitas": [
        "listrik", "pln", "pdam", "air", "wifi", "indihome", "biznet", "firstmedia",
        "telkom", "pulsa", "paket data", "bpjs", "pajak", "asuransi", "iuran",
        "myrepublic", "telkomsel", "indosat", "xl", "tri", "smartfren"
    ],
    "Belanja": [
        "indomaret", "alfamart", "alfamidi", "superindo", "tokopedia", "shopee",
        "lazada", "tiktok shop", "blibli", "mall", "hypermart", "transmart",
        "gramedia", "uniqlo", "h&m", "zara", "miniso", "mr diy", "ace hardware"
    ],
    "Transportasi": [
        "pertamina", "shell", "spbu", "bensin", "bbm", "gojek", "goride", "gocar",
        "grab", "maxim", "kereta", "kai", "krl", "mrt", "lrt", "toll", "tol",
        "parkir", "bluebird", "tiket.com", "traveloka", "garuda"
    ],
    "Kesehatan": [
        "apotek", "kimia farma", "k24", "century", "guardian", "watsons",
        "rumah sakit", "klinik", "dokter", "obat", "lab", "prodia", "halodoc", "alodokter"
    ],
    "Hiburan": [
        "bioskop", "cinema", "xxi", "cgv", "cinepolis", "netflix", "spotify",
        "youtube", "steam", "playstation", "game", "karaoke", "disney", "prime"
    ],
    "Gaji": [
        "gaji", "payroll", "salary", "upah", "honor", "bonus"
    ],
    "Investasi": [
        "bibit", "bareksa", "ajaib", "pluang", "stockbit", "deposito", "reksadana", "saham"
    ],
}


def clean_amount(val_str: str) -> float:
    """Convert Indonesian currency string (e.g. '150.000,00' or '25.000') into clean float."""
    if not val_str:
        return 0.0
    s = re.sub(r"[^\d.,]", "", val_str.strip())
    if not s:
        return 0.0

    # If has both dots and commas (e.g. 1.250.000,00 or 1,250,000.00)
    if "." in s and "," in s:
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_comma > last_dot:
            # 1.500.000,00 format
            s = s.replace(".", "").replace(",", ".")
        else:
            # 1,500,000.00 format
            s = s.replace(",", "")
    elif "." in s:
        # Check if dot is thousand separator: e.g. 50.000 or 1.250.000
        parts = s.split(".")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            s = s.replace(".", "")
        elif len(parts) == 2 and len(parts[1]) != 2:
            # Likely thousand separator if not 2 decimal places
            s = s.replace(".", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")

    try:
        amt = float(s)
        return round(amt, 2)
    except (ValueError, TypeError):
        return 0.0


def parse_date_string(date_str: str) -> str:
    """Normalize date strings into YYYY-MM-DD."""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")

    date_str = date_str.strip()

    # Pattern: 15/11/2026 or 15-11-2026
    m1 = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", date_str)
    if m1:
        d, m, y = m1.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"

    # Pattern: 2026-11-15
    m2 = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", date_str)
    if m2:
        y, m, d = m2.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"

    # Pattern: 15 Nov 2026 / 15 November 2026
    m3 = re.search(r"(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})", date_str)
    if m3:
        d, mon_word, y = m3.groups()
        mon_num = MONTH_MAP.get(mon_word.lower()[:3], "01")
        return f"{y}-{mon_num}-{int(d):02d}"

    return datetime.now().strftime("%Y-%m-%d")


def guess_category(text: str, categories_list=None):
    """Smartly match transaction text to existing category id and name."""
    text_lower = text.lower()
    matched_cat_name = None

    for cat_name, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            matched_cat_name = cat_name
            break

    if not categories_list:
        return None, matched_cat_name or "Lainnya"

    # Match against actual user category objects
    if matched_cat_name:
        for c in categories_list:
            if c["name"].lower() == matched_cat_name.lower():
                return c["id"], c["name"]

    # Partial substring match
    for c in categories_list:
        c_name = c["name"].lower()
        if c_name in text_lower or any(part in c_name for part in text_lower.split() if len(part) >= 3):
            return c["id"], c["name"]

    # Fallback to "Lainnya" or first category
    fallback = next((c for c in categories_list if "lainnya" in c["name"].lower()), categories_list[0] if categories_list else None)
    if fallback:
        return fallback["id"], fallback["name"]

    return None, "Lainnya"


def parse_single_bca_block(text: str, categories_list=None) -> dict:
    """Extract fields from a single BCA notification email text block."""
    # 1. Amount Extraction
    amount = 0.0
    amt_patterns = [
        r"(?:Jumlah|Nominal|Total|Sebesar|Nilai Transaksi)\s*(?:Transaksi)?\s*[:=]?\s*(?:IDR|Rp\.?|RP)?\s*([0-9.,]+)",
        r"(?:IDR|Rp\.?|RP)\s*([0-9.,]+)",
        r"([0-9.,]+)\s*(?:DB|CR)",
    ]
    for pat in amt_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = clean_amount(m.group(1))
            if candidate > 0:
                amount = candidate
                break

    # 2. Date Extraction
    date_patterns = [
        r"(?:Tanggal|Tgl|Waktu)\s*(?:Transaksi)?\s*[:=]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{4})",
        r"(?:Tanggal|Tgl)\s*[:=]?\s*([0-9]{1,2}\s+[a-zA-Z]+\s+[0-9]{4})",
        r"([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{4})",
    ]
    tx_date = datetime.now().strftime("%Y-%m-%d")
    for pat in date_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            tx_date = parse_date_string(m.group(1))
            break

    # 3. Type Extraction (Default expense for payments/QRIS/transfers)
    tx_type = "expense"
    if re.search(r"\b(CR|KREDIT|DANA MASUK|TRANSFER DARI|DITERIMA DARI|UANG MASUK)\b", text, re.IGNORECASE):
        tx_type = "income"
    elif re.search(r"\b(DB|DEBIT|TRANSFER KE|PEMBAYARAN|QRIS|PEMBELIAN|TARIK TUNAI)\b", text, re.IGNORECASE):
        tx_type = "expense"

    # 4. Description Extraction
    desc_parts = []

    # Check merchant
    m_merch = re.search(r"(?:Merchant|Nama Merchant|Penyedia Layanan)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
    if m_merch:
        desc_parts.append(m_merch.group(1).strip())

    # Check destination account / recipient
    m_recip = re.search(r"(?:Rekening Tujuan|Nama Penerima|Tujuan|Ke Rekening)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
    if m_recip:
        desc_parts.append(m_recip.group(1).strip())

    # Check notes / berita
    m_notes = re.search(r"(?:Berita|Catatan|Keterangan)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
    if m_notes:
        note_str = m_notes.group(1).strip()
        if note_str and note_str.lower() != "tidak ada":
            desc_parts.append(note_str)

    # Check transaction type label if no merchant/recipient
    if not desc_parts:
        m_type_lbl = re.search(r"(?:Jenis Transaksi|Tipe Transaksi)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
        if m_type_lbl:
            desc_parts.append(m_type_lbl.group(1).strip())

    description = " - ".join(desc_parts) if desc_parts else ""
    if not description:
        # Fallback snippet
        first_line = text.strip().split("\n")[0][:45]
        description = f"Transaksi BCA ({first_line})" if first_line else "Pengeluaran BCA"

    description = re.sub(r"\s+", " ", description).strip()[:100]

    # 5. Smart Category Matching
    category_id, category_name = guess_category(f"{description} {text[:200]}", categories_list)

    return {
        "amount": amount,
        "date": tx_date,
        "type": tx_type,
        "description": description,
        "category_id": category_id,
        "category_name": category_name,
        "confidence": "high" if amount > 0 else "low",
        "snippet": text.strip()[:160],
    }


def parse_bca_text(raw_text: str, categories_list=None) -> list:
    """Parse text containing one or multiple BCA transactions."""
    if not raw_text or not raw_text.strip():
        return []

    clean_text = raw_text.strip()

    # Check if text contains multiple separated emails or statement rows
    # Common separators in BCA e-statement: date patterns at start of line
    lines = clean_text.splitlines()
    statement_rows = []
    current_block = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Split on header keywords like 'Transaksi Finansial Berhasil', 'Pembayaran QRIS', etc.
        if re.search(r"^(Transaksi|Pembayaran|Notifikasi Transaksi|Bukti Transfer|BCA)\b", stripped, re.IGNORECASE) and current_block:
            statement_rows.append("\n".join(current_block))
            current_block = [stripped]
        else:
            current_block.append(stripped)

    if current_block:
        statement_rows.append("\n".join(current_block))

    # If only 1 block detected, treat whole text as one
    if len(statement_rows) <= 1:
        res = parse_single_bca_block(clean_text, categories_list)
        return [res] if res["amount"] > 0 else [res]

    results = []
    for block in statement_rows:
        parsed = parse_single_bca_block(block, categories_list)
        if parsed["amount"] > 0:
            results.append(parsed)

    return results if results else [parse_single_bca_block(clean_text, categories_list)]


def fetch_bca_emails_imap(host: str, username: str, app_password: str, limit: int = 15, categories_list=None) -> list:
    """Directly fetch and parse recent BCA notification emails via IMAP SSL."""
    parsed_items = []
    try:
        mail = imaplib.IMAP4_SSL(host, port=993)
        mail.login(username, app_password)
        mail.select("INBOX")

        # Search for BCA emails
        status, messages = mail.search(None, '(OR FROM "bca.co.id" FROM "klikbca.com")')
        if status != "OK" or not messages[0]:
            # Try searching by subject
            status, messages = mail.search(None, '(OR SUBJECT "BCA" SUBJECT "Transaksi")')

        if status != "OK" or not messages[0]:
            mail.logout()
            return []

        email_ids = messages[0].split()
        # Take the most recent 'limit' emails
        recent_ids = email_ids[-limit:]

        for eid in reversed(recent_ids):
            res, msg_data = mail.fetch(eid, "(RFC822)")
            if res != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = BytesParser(policy=policy.default).parsebytes(raw_email)

            body_content = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        body_content += part.get_payload(decode=True).decode(errors="ignore") + "\n"
                    elif content_type == "text/html" and not body_content and "attachment" not in content_disposition:
                        html = part.get_payload(decode=True).decode(errors="ignore")
                        # Strip HTML tags
                        body_content += re.sub(r"<[^>]+>", " ", html) + "\n"
            else:
                body_content = msg.get_payload(decode=True).decode(errors="ignore")

            if body_content:
                parsed_list = parse_bca_text(body_content, categories_list)
                for item in parsed_list:
                    if item["amount"] > 0:
                        item["email_subject"] = msg.get("Subject", "Notifikasi BCA")
                        item["email_from"] = msg.get("From", "BCA")
                        parsed_items.append(item)

        mail.logout()
    except Exception as e:
        raise RuntimeError(f"Gagal menghubungkan ke server email: {str(e)}")

    return parsed_items
