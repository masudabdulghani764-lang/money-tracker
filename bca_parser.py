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
        "gaji", "payroll", "salary", "upah", "honor", "bonus", "tunjangan",
        "thr", "komisi", "fee", "insentif", "uang saku", "pt ", "cv "
    ],
    "Investasi": [
        "bibit", "bareksa", "ajaib", "pluang", "stockbit", "deposito",
        "reksadana", "saham", "dividen", "kupon", "yield", "bunga tabungan",
        "bunga bank", "crypto", "binance", "tokocrypto"
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


def guess_category(text: str, categories_list=None, tx_type: str = "expense"):
    """Smartly match transaction text to existing category id and name."""
    text_lower = text.lower()
    matched_cat_name = None

    # Priority category order based on transaction type
    if tx_type == "income":
        order = ["Gaji", "Investasi", "Makanan & Minuman", "Tagihan & Utilitas", "Belanja", "Transportasi", "Kesehatan", "Hiburan"]
    else:
        order = ["Makanan & Minuman", "Tagihan & Utilitas", "Belanja", "Transportasi", "Kesehatan", "Hiburan", "Gaji", "Investasi"]

    for cat_name in order:
        keywords = CATEGORY_KEYWORDS.get(cat_name, [])
        if any(kw in text_lower for kw in keywords):
            matched_cat_name = cat_name
            break

    if not categories_list:
        if tx_type == "income":
            return None, matched_cat_name or "Gaji"
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

    # If income and no direct match, check for income-specific categories
    if tx_type == "income":
        for preferred in ["pemasukan", "pendapatan", "transfer masuk", "dana masuk", "uang masuk"]:
            match = next((c for c in categories_list if preferred in c["name"].lower()), None)
            if match:
                return match["id"], match["name"]

        # Check if text looks like work or salary and user has "Gaji"
        gaji_cat = next((c for c in categories_list if "gaji" in c["name"].lower()), None)
        if gaji_cat and any(w in text_lower for w in ["gaji", "payroll", "pt ", "cv ", "honor", "bonus", "salary"]):
            return gaji_cat["id"], gaji_cat["name"]

    # Fallback to "Lainnya" or first category
    fallback = next((c for c in categories_list if "lainnya" in c["name"].lower()), categories_list[0] if categories_list else None)
    if fallback:
        return fallback["id"], fallback["name"]

    return None, "Lainnya"


def is_location_text(val: str) -> bool:
    """Identify if a candidate string is purely a location/city name instead of a store/platform."""
    if not val:
        return True
    s = val.strip().lower()
    city_keywords = [
        "jakarta", "dki jakarta", "jakarta pusat", "jakarta selatan", "jakarta barat",
        "jakarta timur", "jakarta utara", "surabaya", "bandung", "medan", "semarang",
        "makassar", "palembang", "tangerang", "tangerang selatan", "tangsel", "depok",
        "bekasi", "bogor", "yogyakarta", "jogja", "solo", "surakarta", "malang",
        "denpasar", "bali", "batam", "pekanbaru", "bandar lampung", "padang", "pontianak",
        "banjarmasin", "samarinda", "balikpapan", "manado", "indonesia", "id"
    ]
    if s in city_keywords:
        return True
    if re.match(r"^(?:kota|kab\.?|kabupaten|kec\.?|kecamatan|prov\.?|provinsi|propinsi)\b", s):
        return True
    if re.match(r"^lokasi\b", s):
        return True
    return False


def parse_single_bca_block(text: str, categories_list=None) -> dict:
    """Extract fields from a single BCA notification email text block."""
    # 1. Amount Extraction
    amount = 0.0
    amt_patterns = [
        r"(?:Jumlah|Nominal|Total|Sebesar|Nilai Transaksi|Total Kredit|Total Mutasi|Dana Sebesar)\s*(?:Transaksi)?\s*[:=]?\s*(?:IDR|Rp\.?|RP)?\s*([0-9.,]+)",
        r"(?:IDR|Rp\.?|RP)\s*([0-9.,]+)",
        r"([0-9.,]+)\s*(?:DB|CR|KREDIT|DEBIT)",
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

    # 3. Type Extraction (Income vs Expense)
    tx_type = "expense"

    inflow_patterns = [
        r"\b(transfer\s+(?:dana\s+)?masuk|dana\s+masuk|uang\s+masuk|penerimaan\s+(?:dana|transfer))\b",
        r"\b(setoran\s+tunai|setor\s+tunai|setoran\s+cdm|bi-fast\s+masuk|kliring\s+masuk|incoming\s+transfer)\b",
        r"\b(telah\s+masuk\s+ke\s+rekening|dikreditkan\s+ke\s+rekening|masuk\s+ke\s+rekening)\b",
        r"\b(transfer\s+dari|diterima\s+dari|terima\s+dana|terima\s+transfer)\b",
        r"\b(bunga\s+tabungan|bagi\s+hasil|cashback|refund|pengembalian\s+dana)\b",
        r"\b(payroll\s+masuk|gaji\s+masuk|transfer\s+gaji)\b",
    ]
    is_inflow = any(re.search(pat, text, re.IGNORECASE) for pat in inflow_patterns)

    has_cr_flag = bool(re.search(r"(?:\bCR\b|\(CR\)|mutasi\s+cr|kredit\s*[:=]|total\s+kredit)", text, re.IGNORECASE))
    if not has_cr_flag and re.search(r"\bkredit\b", text, re.IGNORECASE):
        if not re.search(r"\b(?:kartu\s+kredit|credit\s+card|cicilan)\b", text, re.IGNORECASE):
            has_cr_flag = True

    outflow_patterns = [
        r"\b(transfer\s+ke|rekening\s+tujuan|nama\s+penerima|pembayaran(?:\s+ke)?|pembelian|qris|tarik\s+tunai)\b",
        r"\b(tagihan|kartu\s+kredit|debit|autodebet|pendebetan)\b",
        r"(?:\bDB\b|\(DB\)|mutasi\s+db|total\s+debit)",
    ]
    is_outflow = any(re.search(pat, text, re.IGNORECASE) for pat in outflow_patterns)

    if (is_inflow or has_cr_flag) and not is_outflow:
        tx_type = "income"
    elif is_inflow and has_cr_flag:
        tx_type = "income"
    elif is_inflow and not re.search(r"\b(transfer\s+ke|qris|pembayaran\s+ke)\b", text, re.IGNORECASE):
        tx_type = "income"
    elif is_outflow:
        tx_type = "expense"
    else:
        if re.search(r"(?:Pengirim|Dari Rekening|Nama Pengirim)\s*[:=]", text, re.IGNORECASE) and not re.search(r"(?:Penerima|Tujuan|Ke Rekening|Merchant)\s*[:=]", text, re.IGNORECASE):
            tx_type = "income"
        else:
            tx_type = "expense"

    # 4. Description Extraction
    desc_parts = []
    if tx_type == "income":
        sender_name = None
        m_name = re.search(r"(?:Nama Pengirim|Pengirim Dana|Nama Penyetor)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
        if m_name:
            sender_name = m_name.group(1).strip()
        else:
            m_gen = re.search(r"(?:Pengirim|Dari Bank|Dari Rekening|Dari)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
            if m_gen:
                sender_name = m_gen.group(1).strip()

        if sender_name:
            desc_parts.append(f"Dari {sender_name}")

        m_notes = re.search(r"(?:Berita|Catatan|Keterangan)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
        if m_notes:
            note_str = m_notes.group(1).strip()
            if note_str and note_str.lower() not in ["tidak ada", "-", "none"]:
                desc_parts.append(note_str)

        if not desc_parts:
            m_type_lbl = re.search(r"(?:Jenis Transaksi|Tipe Transaksi)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
            if m_type_lbl:
                desc_parts.append(m_type_lbl.group(1).strip())

        description = " - ".join(desc_parts) if desc_parts else "Pemasukan / Transfer Masuk BCA"
    else:
        # Outflow / Pengeluaran: Prioritize specific Store / Platform / Merchant
        store_patterns = [
            r"(?:Nama\s+Merchant|Merchant\s+Name)\s*[:=]\s*([^\r\n]+)",
            r"(?:Nama\s+Toko|Nama\s+Outlet|Nama\s+Platform)\s*[:=]\s*([^\r\n]+)",
            r"(?:Penyedia\s+Jasa|Penyedia\s+Layanan|Nama\s+Perusahaan|Institusi)\s*[:=]\s*([^\r\n]+)",
            r"(?:Nama\s+Biller|Biller)\s*[:=]\s*([^\r\n]+)",
            # Standalone Merchant/Toko/Outlet (make sure not preceded by Lokasi/Kota/Alamat)
            r"(?<!Lokasi\s)(?<!Kota\s)(?<!Alamat\s)\b(?:Merchant|Toko|Outlet|Platform)\s*[:=]\s*([^\r\n]+)",
        ]

        store_val = None
        for pat in store_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip()
                if candidate and not is_location_text(candidate):
                    store_val = candidate
                    break

        if store_val:
            desc_parts.append(store_val)

        # Destination Account / Recipient
        recip_patterns = [
            r"(?:Rekening\s+Tujuan|Nama\s+Penerima|Ke\s+Rekening)\s*[:=]\s*([^\r\n]+)",
        ]
        for pat in recip_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip()
                if candidate and not is_location_text(candidate):
                    if not store_val or candidate.lower() not in store_val.lower():
                        desc_parts.append(candidate)
                    break

        # Notes / Berita / Keterangan (ensure it is not a location or placeholder)
        m_notes = re.search(r"(?:Berita|Catatan|Keterangan)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
        if m_notes:
            note_str = m_notes.group(1).strip()
            if note_str and note_str.lower() not in ["tidak ada", "-", "none"] and not is_location_text(note_str):
                # If no store found yet, clean bank statement prefixes (QRIS, DB, TRSF)
                if not desc_parts:
                    cleaned_note = re.sub(r"^(?:qris|db|trsf|pos)\s+[0-9a-z/]+\s+", "", note_str, flags=re.I).strip()
                    desc_parts.append(cleaned_note if cleaned_note else note_str)
                else:
                    desc_parts.append(note_str)

        if not desc_parts:
            m_type_lbl = re.search(r"(?:Jenis\s+Transaksi|Tipe\s+Transaksi|Jenis\s+Pembayaran)\s*[:=]\s*([^\n\r]+)", text, re.IGNORECASE)
            if m_type_lbl:
                type_val = m_type_lbl.group(1).strip()
                if not is_location_text(type_val):
                    desc_parts.append(type_val)

        description = " - ".join(desc_parts) if desc_parts else "Pengeluaran BCA"

    description = re.sub(r"\s+", " ", description).strip()[:100]

    # 5. Smart Category Matching
    category_id, category_name = guess_category(f"{description} {text[:250]}", categories_list, tx_type=tx_type)

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
