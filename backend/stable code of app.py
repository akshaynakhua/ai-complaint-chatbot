# backend/app.py
import os
import re
import uuid
import time
import logging
import threading
import subprocess
import sqlite3
import random
from datetime import datetime, date
from typing import Tuple, Optional

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import joblib
import shutil

# ---------- session manager ----------
from session_handler import SessionManager

# ---------- Optional extractors ----------
EXTRACTORS_INFO = []
try:
    import fitz  # PyMuPDF
    EXTRACTORS_INFO.append("PyMuPDF")
except Exception:
    fitz = None

try:
    import pdfplumber
    EXTRACTORS_INFO.append("pdfplumber")
except Exception:
    pdfplumber = None

try:
    from PyPDF2 import PdfReader
    EXTRACTORS_INFO.append("PyPDF2")
except Exception:
    PdfReader = None

try:
    from PIL import Image
    import pytesseract
except Exception:
    Image = None
    pytesseract = None

try:
    from pdf2image import convert_from_bytes
except Exception:
    convert_from_bytes = None

try:
    import docx
except Exception:
    docx = None

# ---------- Fuzzy presence flag ----------
try:
    from rapidfuzz import fuzz, process as rf_process  # noqa: F401
    _USE_RF = True
except Exception:
    import difflib  # noqa: F401
    _USE_RF = False

# ---------- Paths & setup ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DB_DIR = os.path.join(BASE_DIR, "db")
os.makedirs(DB_DIR, exist_ok=True)

DB_PATH = os.path.join(DB_DIR, "chatbot.sqlite3")

DATASET_CSV = os.path.join(BASE_DIR, "data", "complaints_dataset_sample.csv")
TRAIN_SCRIPT = os.path.join(BASE_DIR, "train_model.py")

CATEGORY_MODEL_PATH = os.path.join(BASE_DIR, "models", "category_model.joblib")
SUBCATEGORY_MODEL_PATH = os.path.join(BASE_DIR, "models", "sub_category_model.joblib")

# ---------- registries ----------
from registries import brokers as RBro
from registries import exchanges as REx
from registries import Listed_Company_Equity_Issue_DividendTransfer_Transmission_Duplicate_Shares_BonusShares_etc as LCEI
from registries import mutual_funds as RMF
from registries import investment_advisers as RIA  # NEW

# Tesseract (Windows) auto-detect
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if pytesseract:
    if os.path.exists(TESSERACT_CMD):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    else:
        maybe = shutil.which("tesseract.exe")
        if maybe:
            pytesseract.pytesseract.tesseract_cmd = maybe

# Poppler path for pdf2image on Windows (edit if needed)
POPPLER_PATH = os.environ.get("POPPLER_PATH", r"C:\poppler-24.08.0\Library\bin")

# ---------- OTP config ----------
OTP_EXPIRY_SECS = 300
OTP_DEBUG_SHOW_CODE = True  # set False for prod

# ---------- Flask / CORS ----------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

CORS(
    app,
    supports_credentials=True,
    resources={
        r"/chat":      {"origins": ["http://127.0.0.1:*", "http://localhost:*", "null", "*"]},
        r"/uploads/*": {"origins": ["http://127.0.0.1:*", "http://localhost:*", "null", "*"]},
        r"/meta/*":    {"origins": ["http://127.0.0.1:*", "http://localhost:*", "null", "*"]},
    },
    allow_headers=["Content-Type", "X-Requested-With"],
    methods=["GET", "POST", "OPTIONS"],
    max_age=600,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("complaint-bot")
log.info("Using DB_PATH: %s", DB_PATH)

# ---------- Load models ----------
_category_clf = None
_subcategory_clf = None

def load_models():
    global _category_clf, _subcategory_clf
    if os.path.exists(CATEGORY_MODEL_PATH):
        _category_clf = joblib.load(CATEGORY_MODEL_PATH)
        log.info("Category model loaded.")
    else:
        log.warning("Missing category model at %s", CATEGORY_MODEL_PATH)
    if os.path.exists(SUBCATEGORY_MODEL_PATH):
        _subcategory_clf = joblib.load(SUBCATEGORY_MODEL_PATH)
        log.info("Sub-category model loaded.")
    else:
        log.warning("Missing sub-category model at %s", SUBCATEGORY_MODEL_PATH)

load_models()

# ---------- Ensure DB/table exists ----------
def _ensure_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        complaint_number TEXT,
        description TEXT,
        category TEXT,
        sub_category TEXT,
        attachment_path TEXT,
        full_name TEXT,
        phone TEXT,
        email TEXT,
        pan TEXT,
        address TEXT,
        dob TEXT,
        broker_name TEXT,
        exchange_name TEXT,
        client_or_dp TEXT,
        company_name TEXT,
        holding_mode TEXT,
        folio_number TEXT,
        demat_account_number TEXT,
        mutual_fund_name TEXT,
        investment_advisor_name TEXT,
        timestamp TEXT
    )
    """)
    conn.commit()
    conn.close()
    log.info("DB ready at %s", DB_PATH)

_ensure_db()

# ---------- Boot registries ----------
try:
    b_count = RBro.load_registry()
    e_count = REx.load_registry()
    c_count = LCEI.load_registry()
    m_count = RMF.load_registry()
    ia_count = RIA.load_registry()
    log.info(
        "Registries loaded → brokers: %d | exchanges: %d | listed_companies: %d | mutual_funds: %d | advisers: %d",
        b_count, e_count, c_count, m_count, ia_count
    )
except Exception as e:
    log.exception("Failed to load registries: %s", e)

# ---------- Session manager ----------
session_mgr = SessionManager()

# ---------- Helpers ----------
GREETING_RE = re.compile(
    r"^(hi|hello|hey|hii|hlo|helo|namaste|hola|yo|good\s*(morning|evening|afternoon))[\W_]*$",
    re.I
)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
DOC_EXTS   = {".docx"}
PDF_EXTS   = {".pdf"}
ALLOWED_FILE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx"}

YES = {"yes","y","ok","okay","confirm","confirmed"}
NO  = {"no","n","nah","nope"}

START_PHRASES = {"start"}
CLOSE_PHRASES = {"done","bye","goodbye","thank you","thanks","no thank you","exit","quit","finished","end"}

NOT_SURE = {"not sure", "dont know", "don't know", "na", "n/a", "skip", "idk"}

def is_greeting(text: str) -> bool:
    return bool(GREETING_RE.match((text or "").strip()))

def clean_text(t: str) -> str:
    return re.sub(r"[ \t]+", " ", (t or "")).strip()

def format_block(text: str, max_chars: int = 4000) -> str:
    t = (text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if not t:
        return ""
    lines = []
    for raw in t.split("\n"):
        s = raw.strip()
        if not s:
            lines.append("")
            continue
        if re.match(r"^[-•*]\s+", s):
            lines.append(s)
        else:
            while len(s) > 120:
                cut = s.rfind(" ", 0, 120)
                if cut == -1:
                    cut = 120
                lines.append(s[:cut])
                s = s[cut:].lstrip()
            lines.append(s)
    out = "\n".join(lines).strip()
    return out[:max_chars] + ("…" if len(out) > max_chars else "")

def _is_allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename or "")[1].lower()
    return ext in ALLOWED_FILE_EXTS

def save_upload(fs) -> Optional[str]:
    if not fs or not fs.filename or not _is_allowed_file(fs.filename):
        return None
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", fs.filename)
    fname = f"{uuid.uuid4().hex}_{safe}"
    path = os.path.join(UPLOAD_DIR, fname)
    fs.save(path)
    return path

# ---------- Extractors ----------
def extract_text_from_pdf(path: str) -> str:
    text = ""
    if fitz:
        try:
            with fitz.open(path) as doc:
                text = clean_text("\n".join([p.get_text() or "" for p in doc]))
        except Exception as e:
            log.exception("PyMuPDF extract failed: %s", e)
    if not text and pdfplumber:
        try:
            with pdfplumber.open(path) as pdf:
                text = clean_text("\n".join([p.extract_text() or "" for p in pdf.pages]))
        except Exception as e:
            log.exception("pdfplumber extract failed: %s", e)
    if not text and PdfReader:
        try:
            reader = PdfReader(path)
            text = clean_text("\n".join([(p.extract_text() or "") for p in reader.pages]))
        except Exception as e:
            log.exception("PyPDF2 extract failed: %s", e)
    if text and len(text) >= 40:
        return text
    if convert_from_bytes and pytesseract and Image:
        try:
            with open(path, "rb") as fh:
                pdf_bytes = fh.read()
            pages = convert_from_bytes(pdf_bytes, dpi=300, poppler_path=POPPLER_PATH) \
                    if POPPLER_PATH else convert_from_bytes(pdf_bytes, dpi=300)
            chunks = []
            for im in pages:
                try:
                    chunks.append(pytesseract.image_to_string(im, config="--psm 6") or "")
                except Exception as e:
                    log.exception("OCR page failed: %s", e)
            ocr_text = clean_text("\n".join(chunks))
            if ocr_text:
                return ocr_text
        except Exception as e:
            log.exception("pdf2image OCR failed: %s", e)
    return text or ""

def extract_text_from_image(path: str) -> str:
    if not (Image and pytesseract):
        return ""
    try:
        img = Image.open(path).convert("RGB")
        return clean_text(pytesseract.image_to_string(img, config="--psm 6"))
    except Exception as e:
        log.exception("Image OCR failed: %s", e)
        return ""

def extract_text_from_docx(path: str) -> str:
    if not docx:
        return ""
    try:
        d = docx.Document(path)
        return clean_text("\n".join(p.text for p in d.paragraphs))
    except Exception as e:
        log.exception("DOCX extract failed: %s", e)
        return ""

def extract_text_from_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(path)
    if ext in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        return extract_text_from_image(path)
    if ext == ".docx":
        return extract_text_from_docx(path)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return clean_text(f.read())
    except Exception:
        return ""

# ---------- ML prediction helpers ----------
def predict_both(text: str):
    if not text or _category_clf is None or _subcategory_clf is None:
        return None, None
    try:
        cat = _category_clf.predict([text])[0]
        sub = _subcategory_clf.predict([text])[0]
        return cat, sub
    except Exception as e:
        log.exception("Prediction error: %s", e)
        return None, None

def append_to_csv(complaint_text: str, category: str, sub_category: str):
    header_needed = not os.path.exists(DATASET_CSV)
    for _ in range(3):
        try:
            os.makedirs(os.path.dirname(DATASET_CSV), exist_ok=True)
            with open(DATASET_CSV, "a", encoding="utf-8") as f:
                if header_needed:
                    f.write("complaint_text,category,sub_category\n")
                    header_needed = False
                safe = (complaint_text or "").replace('"', '""')
                f.write(f"\"{safe}\",{category},{sub_category}\n")
            return
        except PermissionError:
            time.sleep(0.5)

def retrain_async():
    def _run():
        try:
            import sys
            log.info("Retraining models…")
            subprocess.run([sys.executable, TRAIN_SCRIPT], cwd=BASE_DIR, check=True)
            load_models()
            log.info("Retrain complete.")
        except Exception as e:
            log.exception("Retrain failed: %s", e)
    threading.Thread(target=_run, daemon=True).start()

def lodge_complaint(description, category, sub_category, attachment_path, details) -> str:
    cmp_no = "CMP-" + datetime.utcnow().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8].upper()
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO complaints (
                complaint_number, description, category, sub_category, attachment_path,
                full_name, phone, email, pan, address, dob,
                broker_name, exchange_name, client_or_dp,
                company_name, holding_mode, folio_number, demat_account_number,
                mutual_fund_name, investment_advisor_name,
                timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cmp_no,
            description or "",
            category or "",
            sub_category or "",
            attachment_path or "",
            (details or {}).get("full_name", ""),
            (details or {}).get("phone", ""),
            (details or {}).get("email", ""),
            (details or {}).get("pan", ""),
            (details or {}).get("address", ""),
            (details or {}).get("dob", ""),
            (details or {}).get("broker_name", ""),
            (details or {}).get("exchange_name", ""),
            (details or {}).get("client_or_dp", ""),
            (details or {}).get("company_name", ""),
            (details or {}).get("holding_mode", ""),
            (details or {}).get("folio_number", ""),
            (details or {}).get("demat_account_number", ""),
            (details or {}).get("mutual_fund_name", ""),
            (details or {}).get("investment_advisor_name", ""),
            ts
        ))
        conn.commit()
        conn.close()
        log.info("Complaint saved: %s", cmp_no)
    except Exception as e:
        log.exception("Failed to save complaint %s: %s", cmp_no, e)

    return cmp_no

def pack_response(cid, messages, **extra):
    payload = {
        "cid": cid,
        "messages": messages,
        "response": messages[0] if messages else "",
    }
    if extra.get("attachment_url"):
        payload["file_url"] = extra["attachment_url"]
    payload.update(extra)
    return jsonify(payload)

# ---------- Validators / prompts ----------
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$", re.I)
PHONE_RE = re.compile(r"^\+?\d{10,14}$")
EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)

CLIENT_OR_DP_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{4,24}$", re.I)

# Listed Company inputs
FOLIO_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{4,24}$", re.I)
DEMAT_ACCT_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{6,24}$", re.I)

CLIENT_OR_DP_PROMPT = (
    "🧾 Please enter your **Client ID** (trading) **or** **DP ID** (demat).\n"
    "If you don’t have it, type **'no'**."
)

def normalize_dob(s: str) -> Optional[str]:
    s = s.strip()
    m1 = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)   # YYYY-MM-DD
    m2 = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)   # DD/MM/YYYY
    try:
        if m1:
            y, mo, d = map(int, m1.groups())
        elif m2:
            d, mo, y = map(int, m2.groups())
        else:
            return None
        dt = date(y, mo, d)
        return dt.isoformat()
    except Exception:
        return None

def age_years(iso_date: str) -> int:
    y, m, d = map(int, iso_date.split("-"))
    dob = date(y, m, d)
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

DETAIL_STEPS = [
    ("full_name", "👤 Please enter your Full Name (as per PAN):"),
    ("phone",     "📞 Please enter your Phone number:"),
    ("email",     "✉️ Please enter your Email ID:"),
    ("pan",       "🪪 Please enter your PAN (e.g., ABCDE1234F):"),
    ("address",   "🏠 Please enter your Address:"),
    ("dob",       "🎂 Please enter your Date of Birth (YYYY-MM-DD or DD/MM/YYYY):"),
]

def ask_current_detail(st) -> str:
    key, prompt = DETAIL_STEPS[st["details_step_index"]]
    return prompt

# ---------- OTP helpers ----------
def _gen_otp() -> str:
    return f"{random.randint(0, 999999):06d}"

def _begin_otp(st, target: str):
    st["otp"]["target"] = target
    st["otp"][target]["code"] = _gen_otp()
    st["otp"][target]["ts"] = time.time()
    st["otp"][target]["verified"] = False

def _check_otp(st, target: str, code: str) -> Optional[str]:
    data = st["otp"][target]
    if not data["code"]:
        return "No OTP in progress. Type 'resend' to get a new OTP."
    if time.time() - data["ts"] > OTP_EXPIRY_SECS:
        return "OTP expired. Type 'resend' to get a new OTP."
    if code != data["code"]:
        return "Incorrect OTP. Please try again or type 'resend'."
    data["verified"] = True
    data["code"] = None
    data["ts"] = 0
    return None

def handle_detail_input(st, user_text: str) -> Optional[str]:
    key, _prompt = DETAIL_STEPS[st["details_step_index"]]
    val = (user_text or "").strip()

    if key == "full_name":
        if len(val) < 2:
            return "Name looks too short. Please enter your Full Name (as per PAN):"
        st["details"]["full_name"] = val
        st["details_step_index"] += 1
        return None

    if key == "phone":
        if not PHONE_RE.match(val):
            return "Please enter a valid phone number, e.g. +9198XXXXXXXX or 98XXXXXXXX."
        st["details"]["phone"] = val
        _begin_otp(st, "phone")
        st["stage"] = "verify_otp"
        return None

    if key == "email":
        if not EMAIL_RE.match(val):
            return "Please enter a valid Email ID (e.g., name@example.com):"
        st["details"]["email"] = val
        _begin_otp(st, "email")
        st["stage"] = "verify_otp"
        return None

    if key == "pan":
        if not PAN_RE.match(val):
            return "PAN looks invalid. Please enter like ABCDE1234F:"
        st["details"]["pan"] = val.upper()
        st["details_step_index"] += 1
        return None

    if key == "address":
        if len(val) < 5:
            return "Address looks too short. Please enter your Address:"
        st["details"]["address"] = val
        st["details_step_index"] += 1
        return None

    if key == "dob":
        norm = normalize_dob(val)
        if not norm:
            return "DOB looks invalid. Use YYYY-MM-DD or DD/MM/YYYY.\nPlease enter your Date of Birth:"
        if age_years(norm) < 18:
            return "DOB looks too recent. You must be at least 18 years old.\nPlease enter your Date of Birth:"
        st["details"]["dob"] = norm
        st["details_step_index"] += 1
        return None

    return "Unexpected field."

# ---------- Case-insensitive candidate helpers ----------
def _list_brokers() -> list[str]:
    return list(getattr(RBro, "_BROKER_LIST", []))

def _list_exchanges() -> list[str]:
    return list(getattr(REx, "_EXCH_LIST", []))

def _list_companies() -> list[str]:
    return list(getattr(LCEI, "_COMPANY_LIST", []) or getattr(LCEI, "_LIST", []))

def _list_mutualfunds() -> list[str]:
    return list(getattr(RMF, "_FUND_LIST", []) or getattr(RMF, "_MF_LIST", []))

def _list_advisers() -> list[str]:
    return list(getattr(RIA, "_ADVISER_LIST", []) or getattr(RIA, "_ADVISOR_LIST", []))

def _render_choices(options: list[str]) -> str:
    lines = [f"{i+1}) {opt}" for i, opt in enumerate(options)]
    lines.append(f"{len(options)+1}) None of these")
    return "\n".join(lines)

def broker_candidates(q: str, k: int = 8) -> list[str]:
    qn = (q or "").strip().lower()
    if not qn:
        return []
    for name in _list_brokers():
        if name.lower() == qn:
            return [name]
    out_pairs: list[tuple[str, float]] = []
    try:
        for name, score in RBro.suggest(q or ""):
            if score >= 55.0:
                out_pairs.append((name, float(score)))
                if len(out_pairs) >= k:
                    break
    except Exception:
        pass
    if out_pairs:
        best: dict[str, float] = {}
        for n, s in out_pairs:
            if n not in best or s > best[n]:
                best[n] = s
        return sorted(best.keys(), key=lambda x: best[x], reverse=True)[:k]
    names = _list_brokers()
    prefix = [n for n in names if len(qn) >= 3 and n.lower().startswith(qn[:3])]
    contains = [n for n in names if qn in n.lower()]
    seen, final = set(), []
    for n in (prefix + contains):
        if n not in seen:
            final.append(n); seen.add(n)
        if len(final) >= k:
            break
    return final

def exchange_suggestions(q: str, k: int = 6) -> list[str]:
    qn = (q or "").strip().lower()
    if not qn:
        return []
    for name in _list_exchanges():
        if name.lower() == qn:
            return [name]
    out: list[str] = []
    try:
        for name, score in REx.suggest(q or ""):
            if score >= 55.0:
                out.append(name)
                if len(out) >= k:
                    break
    except Exception:
        pass
    if out:
        seen, uniq = set(), []
        for n in out:
            if n not in seen:
                uniq.append(n); seen.add(n)
        return uniq[:k]
    names = _list_exchanges()
    prefix = [n for n in names if len(qn) >= 3 and n.lower().startswith(qn[:3])]
    contains = [n for n in names if qn in n.lower()]
    seen, final = set(), []
    for n in (prefix + contains):
        if n not in seen:
            final.append(n); seen.add(n)
        if len(final) >= k:
            break
    return final

def company_candidates(q: str, k: int = 8) -> list[str]:
    qn = (q or "").strip().lower()
    if not qn:
        return []
    for name in _list_companies():
        if name.lower() == qn:
            return [name]
    out: list[str] = []
    try:
        for name, score in LCEI.suggest(q or ""):
            if score >= 55.0:
                out.append(name)
                if len(out) >= k:
                    break
    except Exception:
        pass
    if out:
        seen, uniq = set(), []
        for n in out:
            if n not in seen:
                uniq.append(n); seen.add(n)
        return uniq[:k]
    names = _list_companies()
    prefix = [n for n in names if len(qn) >= 3 and n.lower().startswith(qn[:3])]
    contains = [n for n in names if qn in n.lower()]
    seen, final = set(), []
    for n in (prefix + contains):
        if n not in seen:
            final.append(n); seen.add(n)
        if len(final) >= k:
            break
    return final

def mutualfund_candidates(q: str, k: int = 8) -> list[str]:
    qn = (q or "").strip().lower()
    if not qn:
        return []
    for name in _list_mutualfunds():
        if name.lower() == qn:
            return [name]
    out: list[str] = []
    try:
        for name, score in RMF.suggest(q or ""):
            if score >= 55.0:
                out.append(name)
                if len(out) >= k:
                    break
    except Exception:
        pass
    if out:
        seen, uniq = set(), []
        for n in out:
            if n not in seen:
                uniq.append(n); seen.add(n)
        return uniq[:k]
    names = _list_mutualfunds()
    prefix = [n for n in names if len(qn) >= 3 and n.lower().startswith(qn[:3])]
    contains = [n for n in names if qn in n.lower()]
    seen, final = set(), []
    for n in (prefix + contains):
        if n not in seen:
            final.append(n); seen.add(n)
        if len(final) >= k:
            break
    return final

def advisor_candidates(q: str, k: int = 8) -> list[str]:
    qn = (q or "").strip().lower()
    if not qn:
        return []
    for name in _list_advisers():
        if name.lower() == qn:
            return [name]
    out: list[str] = []
    try:
        for name, score in RIA.suggest(q or ""):
            if score >= 55.0:
                out.append(name)
                if len(out) >= k:
                    break
    except Exception:
        pass
    if out:
        seen, uniq = set(), []
        for n in out:
            if n not in seen:
                uniq.append(n); seen.add(n)
        return uniq[:k]
    names = _list_advisers()
    prefix = [n for n in names if len(qn) >= 3 and n.lower().startswith(qn[:3])]
    contains = [n for n in names if qn in n.lower()]
    seen, final = set(), []
    for n in (prefix + contains):
        if n not in seen:
            final.append(n); seen.add(n)
        if len(final) >= k:
            break
    return final

# ---------- Case-insensitive validators ----------
def validate_broker(name: str) -> tuple[bool, Optional[str]]:
    qn = (name or "").strip().lower()
    if not qn:
        return False, None
    for b in _list_brokers():
        if b.lower() == qn:
            return True, b
    full = RBro.resolve_full_name(name or "")
    if full:
        return True, full
    sug = RBro.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if score >= 92.0:
            return True, cand
    return False, None

def validate_exchange(name: str) -> tuple[bool, Optional[str]]:
    qn = (name or "").strip().lower()
    if not qn:
        return False, None
    for e in _list_exchanges():
        if e.lower() == qn:
            return True, e
    full = REx.resolve_full_name(name or "")
    if full:
        return True, full
    sug = REx.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if score >= 90.0:
            return True, cand
    return False, None

def validate_company(name: str) -> tuple[bool, Optional[str]]:
    qn = (name or "").strip().lower()
    if not qn:
        return False, None
    for c in _list_companies():
        if c.lower() == qn:
            return True, c
    full = LCEI.resolve_full_name(name or "")
    if full:
        return True, full
    sug = LCEI.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if score >= 90.0:
            return True, cand
    return False, None

def validate_mutualfund(name: str) -> tuple[bool, Optional[str]]:
    qn = (name or "").strip().lower()
    if not qn:
        return False, None
    for m in _list_mutualfunds():
        if m.lower() == qn:
            return True, m
    full = RMF.resolve_full_name(name or "")
    if full:
        return True, full
    sug = RMF.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if score >= 90.0:
            return True, cand
    return False, None

def validate_advisor(name: str) -> tuple[bool, Optional[str]]:
    qn = (name or "").strip().lower()
    if not qn:
        return False, None
    for a in _list_advisers():
        if a.lower() == qn:
            return True, a
    full = RIA.resolve_full_name(name or "")
    if full:
        return True, full
    sug = RIA.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if score >= 90.0:
            return True, cand
    return False, None

# ---------- Auto-detect broker/exchange from complaint text ----------
def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (s or "").upper()).strip()

def _best_one(q: str, choices: list[str]) -> tuple[int, Optional[str]]:
    if not choices:
        return 0, None
    if _USE_RF:
        best = rf_process.extractOne(q, choices, scorer=fuzz.partial_ratio)
        if not best:
            return 0, None
        name, score, _ = best
        return int(score), name
    else:
        if not q:
            return 0, None
        import difflib
        cand = difflib.get_close_matches(q, choices, n=1, cutoff=0.0)
        if not cand:
            return 0, None
        s = int(100 * difflib.SequenceMatcher(None, q, cand[0]).ratio())
        return s, cand[0]

def detect_broker_and_exchange_from_text(text: str) -> tuple[Optional[str], Optional[str]]:
    t = _norm(text)
    broker_names = [b.upper() for b in _list_brokers()]
    b_score, b_hit = _best_one(t, broker_names)
    broker = b_hit if b_score >= 86 else None

    exch_full = [e.upper() for e in _list_exchanges()]
    e_score, e_hit = _best_one(t, exch_full)
    exchange = e_hit if e_score >= 86 else None

    if broker:
        for b in _list_brokers():
            if b.upper() == broker:
                broker = b
                break
    if exchange:
        for e in _list_exchanges():
            if e.upper() == exchange:
                exchange = e
                break

    return broker, exchange

# ---------- Client/DP step for Stock Broker ----------
def _should_ask_client_dp(st) -> bool:
    return (st.get("pred_category") or "").strip().lower() == "stock broker" \
        and bool(st["details"].get("broker_name")) and bool(st["details"].get("exchange_name"))

def _goto_client_dp(st, cid):
    st["stage"] = "ask_client_dp"
    session_mgr.update_session(cid, st)
    return pack_response(cid, [CLIENT_OR_DP_PROMPT], stage=st["stage"])

# ---------- Meta endpoints for UI (optional) ----------
@app.get("/meta/brokers/suggest")
def brokers_suggest():
    q = (request.args.get("q") or "").strip()
    out = [{"name": n} for n in (broker_candidates(q) if q else [])]
    return jsonify({"items": out})

@app.get("/meta/exchanges/suggest")
def exchanges_suggest():
    q = (request.args.get("q") or "").strip()
    out = [{"name": n} for n in (exchange_suggestions(q) if q else [])]
    return jsonify({"items": out})

@app.get("/meta/companies/suggest")
def companies_suggest():
    q = (request.args.get("q") or "").strip()
    out = [{"name": n} for n in (company_candidates(q) if q else [])]
    return jsonify({"items": out})

@app.get("/meta/mutualfunds/suggest")
def mutualfunds_suggest():
    q = (request.args.get("q") or "").strip()
    out = [{"name": n} for n in (mutualfund_candidates(q) if q else [])]
    return jsonify({"items": out})

@app.get("/meta/advisers/suggest")
def advisers_suggest():
    q = (request.args.get("q") or "").strip()
    out = [{"name": n} for n in (advisor_candidates(q) if q else [])]
    return jsonify({"items": out})

# ---------- File & health ----------
@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)

@app.route("/health")
def health():
    return jsonify({"ok": True, "extractors": EXTRACTORS_INFO})

# ---------- Session helpers ----------
def get_cid_and_state() -> Tuple[str, dict]:
    ctype = (request.content_type or "").lower()
    if "multipart/form-data" in ctype:
        cid = (request.form.get("cid") or "").strip()
    else:
        data = request.get_json(silent=True) or {}
        cid = (data.get("cid") or "").strip()
    cid = session_mgr.ensure_session_id(cid)
    st = session_mgr.get_session(cid)
    return cid, st

def safe_init_details(st):
    st.setdefault("details", {})
    st.setdefault("otp", {
        "target": None,
        "phone": {"code": None, "ts": 0, "verified": False},
        "email": {"code": None, "ts": 0, "verified": False},
    })
    if "details_step_index" not in st:
        st["details_step_index"] = 0

# ---------- Main chat ----------
@app.route("/chat", methods=["POST"])
def chat():
    try:
        cid, st = get_cid_and_state()
        safe_init_details(st)

        user_msg = ""
        file_url = None
        incoming_path = None
        ctype = (request.content_type or "").lower()

        if "multipart/form-data" in ctype:
            user_msg = (request.form.get("message") or "").strip()
            f = request.files.get("file")
            if f and f.filename:
                incoming_path = save_upload(f)
                if incoming_path:
                    st["attachment_path"] = incoming_path
                    file_url = f"/uploads/{os.path.basename(incoming_path)}"
        else:
            data = request.get_json(silent=True) or {}
            user_msg = (data.get("message") or "").strip()

        # ---------- Completed / Ended ----------
        if st["stage"] == "completed":
            low = (user_msg or "").strip().lower()
            if not low:
                return pack_response(cid, [], stage="completed")
            if low in START_PHRASES:
                st = session_mgr.reset_session(cid)
                return pack_response(cid, ["Hi! 👋 Please describe your complaint in a sentence or two, or upload a PDF/image/DOCX."], stage="awaiting_description")
            if (low in CLOSE_PHRASES) or (low in NO):
                st["stage"] = "ended"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["🙏 Thank you. Your session is now closed."], stage="ended")
            return pack_response(cid, ["Need to raise another complaint? Type 'start'. Say 'done' to end."], stage="completed")

        if st["stage"] == "ended":
            low = (user_msg or "").strip().lower()
            if low in START_PHRASES:
                st = session_mgr.reset_session(cid)
                return pack_response(cid, ["Hi! 👋 Please describe your complaint in a sentence or two, or upload a PDF/image/DOCX."], stage="awaiting_description")
            return pack_response(cid, ["Session is closed. Type 'start' to raise a new complaint."], stage="ended")

        # ---------- New file → OCR preview ----------
        if incoming_path and st["stage"] in ("awaiting_description", "ocr_preview"):
            text = extract_text_from_file(incoming_path)
            if text:
                st["ocr_text"] = text
                st["stage"] = "ocr_preview"
                session_mgr.update_session(cid, st)
                pretty = format_block(text)
                return pack_response(
                    cid,
                    [f"Here’s what I read from your file:\n\n{pretty}\n\nIs this your complaint text? (yes/no)"],
                    attachment_url=file_url,
                    stage=st["stage"]
                )
            else:
                st["stage"] = "awaiting_description"
                session_mgr.update_session(cid, st)
                return pack_response(
                    cid,
                    ["I couldn't read text from the file. Please re-describe your complaint in text."],
                    attachment_url=file_url,
                    stage=st["stage"]
                )

        # ---------- OCR preview ----------
        if st["stage"] == "ocr_preview":
            low = (user_msg or "").lower()
            if low in YES:
                st["description"] = st.get("ocr_text")
                st["source"] = "file"
                st["ocr_text"] = None
                st["stage"] = "confirming"
                cat, sub = predict_both(st["description"])
                st["pred_category"], st["pred_sub_category"] = cat, sub

                msgs = []
                if cat == "Stock Broker":
                    b, e = detect_broker_and_exchange_from_text(st["description"])
                    if b and not st["details"].get("broker_name") and not st.get("pending_broker"):
                        st["pending_broker"] = b
                        msgs.append(f"✅ Detected broker candidate: **{b}** (will confirm with you).")
                    if e and not st["details"].get("exchange_name") and not st.get("pending_exchange"):
                        st["pending_exchange"] = e
                        msgs.append(f"✅ Detected exchange candidate: **{e}** (will confirm with you).")
                if cat and "listed" in cat.lower():
                    hits = LCEI.autodetect_in_text(st["description"])
                    if hits and not st["details"].get("company_name") and not st.get("pending_company"):
                        st["pending_company"] = hits[0]
                        msgs.append(f"✅ Detected company candidate: **{hits[0]}** (will confirm with you).")
                if cat and "mutual fund" in cat.lower():
                    hits = RMF.autodetect_in_text(st["description"])
                    if hits and not st["details"].get("mutual_fund_name") and not st.get("pending_mutualfund"):
                        st["pending_mutualfund"] = hits[0]
                        msgs.append(f"✅ Detected mutual fund candidate: **{hits[0]}** (will confirm with you).")
                if cat and ("investment" in cat.lower()) and (("adviser" in cat.lower()) or ("advisor" in cat.lower())):
                    hits = RIA.autodetect_in_text(st["description"])
                    if hits and not st["details"].get("investment_advisor_name") and not st.get("pending_advisor"):
                        st["pending_advisor"] = hits[0]
                        msgs.append(f"✅ Detected adviser candidate: **{hits[0]}** (will confirm with you).")

                session_mgr.update_session(cid, st)
                guess = (
                    "🔎 Classification looks like:\n"
                    f"📁 Category → {cat}\n"
                    f"📂 Sub-category → {sub}\n"
                    "Is this correct? (yes / no)"
                )
                return pack_response(cid, msgs + [guess], stage=st["stage"])

            if low in NO:
                st.update({"ocr_text": None, "description": None, "pred_category": None, "pred_sub_category": None})
                st["source"] = "file"
                st["stage"] = "awaiting_description"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["No problem. Please re-describe your complaint in text."], stage=st["stage"])

            return pack_response(cid, ["Please reply with 'yes' or 'no'."], stage=st["stage"])

        # ---------- Awaiting description ----------
        if st["stage"] == "awaiting_description":
            if is_greeting(user_msg):
                return pack_response(cid, ["Hey! 👋 Please describe your complaint, or upload a PDF/image/DOCX."], stage=st["stage"])
            if user_msg:
                st["description"] = clean_text(user_msg)
                st["source"] = "text"
                st["stage"] = "confirming"
                cat, sub = predict_both(st["description"])
                st["pred_category"], st["pred_sub_category"] = cat, sub

                msgs = []
                if cat == "Stock Broker":
                    b, e = detect_broker_and_exchange_from_text(st["description"])
                    if b and not st["details"].get("broker_name") and not st.get("pending_broker"):
                        st["pending_broker"] = b
                        msgs.append(f"✅ Detected broker candidate: **{b}** (will confirm with you).")
                    if e and not st["details"].get("exchange_name") and not st.get("pending_exchange"):
                        st["pending_exchange"] = e
                        msgs.append(f"✅ Detected exchange candidate: **{e}** (will confirm with you).")
                if cat and "listed" in cat.lower():
                    hits = LCEI.autodetect_in_text(st["description"])
                    if hits and not st["details"].get("company_name") and not st.get("pending_company"):
                        st["pending_company"] = hits[0]
                        msgs.append(f"✅ Detected company candidate: **{hits[0]}** (will confirm with you).")
                if cat and "mutual fund" in cat.lower():
                    hits = RMF.autodetect_in_text(st["description"])
                    if hits and not st["details"].get("mutual_fund_name") and not st.get("pending_mutualfund"):
                        st["pending_mutualfund"] = hits[0]
                        msgs.append(f"✅ Detected mutual fund candidate: **{hits[0]}** (will confirm with you).")
                if cat and ("investment" in cat.lower()) and (("adviser" in cat.lower()) or ("advisor" in cat.lower())):
                    hits = RIA.autodetect_in_text(st["description"])
                    if hits and not st["details"].get("investment_advisor_name") and not st.get("pending_advisor"):
                        st["pending_advisor"] = hits[0]
                        msgs.append(f"✅ Detected adviser candidate: **{hits[0]}** (will confirm with you).")

                session_mgr.update_session(cid, st)
                guess = (
                    "🔎 Classification looks like:\n"
                    f"📁 Category → {cat}\n"
                    f"📂 Sub-category → {sub}\n"
                    "Is this correct? (yes / no)"
                )
                return pack_response(cid, msgs + [guess], stage=st["stage"])

            return pack_response(cid, ["Please describe your complaint (you can attach a file too)."], stage=st["stage"])

        # ---------- Confirming category/subcategory ----------
        if st["stage"] == "confirming":
            low = (user_msg or "").lower()
            if low in YES:
                cat = (st.get("pred_category") or "").strip().lower()

                if cat == "stock broker":
                    if st.get("pending_broker"):
                        st["stage"] = "confirm_broker"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"🔎 I detected **{st['pending_broker']}**. Is this the correct broker? (yes / no)"], stage=st["stage"])
                    st["stage"] = "ask_broker"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, ["🏢 Please tell me the Stock Broker name (as registered)."], stage="ask_broker")

                if "listed" in cat and "equity" in cat:
                    if st.get("pending_company"):
                        st["stage"] = "confirm_company"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"🔎 I detected **{st['pending_company']}**. Is this the correct company? (yes / no)"], stage=st["stage"])
                    st["stage"] = "ask_company"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, ["🏢 Please tell me the **Listed Company** name."], stage="ask_company")

                if "mutual fund" in cat:
                    if st.get("pending_mutualfund"):
                        st["stage"] = "confirm_mutualfund"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"🔎 I detected **{st['pending_mutualfund']}**. Is this the correct Mutual Fund? (yes / no)"], stage=st["stage"])
                    st["stage"] = "ask_mutualfund"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, ["🏦 Please tell me the **Mutual Fund** name."], stage="ask_mutualfund")

                if "investment" in cat and (("adviser" in cat) or ("advisor" in cat)):
                    if st.get("pending_advisor"):
                        st["stage"] = "confirm_advisor"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"🔎 I detected **{st['pending_advisor']}**. Is this the correct Investment Adviser? (yes / no)"], stage=st["stage"])
                    st["stage"] = "ask_advisor"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, ["🧑‍💼 Please tell me the **Investment Adviser** name."], stage="ask_advisor")

                st["stage"] = "waiting_file"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["If you have any supporting file/screenshot, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])

            if low in NO:
                came_from_file = (st.get("source") == "file")
                st.update({"pred_category": None, "pred_sub_category": None, "description": None})
                st["stage"] = "awaiting_description"
                session_mgr.update_session(cid, st)
                msg = "No problem. Please re-describe your complaint in text." if came_from_file else "Okay, please re-describe your complaint."
                return pack_response(cid, [msg], stage=st["stage"])

            return pack_response(cid, ["Please reply with 'yes' or 'no' about the classification."], stage=st["stage"])

        # ===================== STOCK BROKER FLOW =====================
        if st["stage"] == "confirm_broker":
            low = (user_msg or "").strip().lower()
            if low in YES and st.get("pending_broker"):
                st["details"]["broker_name"] = st.pop("pending_broker")
                if not st["details"].get("exchange_name"):
                    if st.get("pending_exchange"):
                        st["stage"] = "confirm_exchange"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"🔎 I detected **{st['pending_exchange']}**. Is this the correct exchange? (yes / no)"], stage=st["stage"])
                    st["stage"] = "ask_exchange"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, ["🏛 Please tell me the Stock Exchange (e.g., NSE/BSE or full name)."], stage=st["stage"])

                if _should_ask_client_dp(st):
                    return _goto_client_dp(st, cid)

                st["stage"] = "waiting_file"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"✅ Broker confirmed: **{st['details']['broker_name']}**\n\nIf you have a supporting file, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])

            if low in NO:
                st.pop("pending_broker", None)
                st["stage"] = "ask_broker"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["Okay. Please type the **registered Stock Broker** name."], stage=st["stage"])

            return pack_response(cid, ["Please reply with 'yes' or 'no'."], stage=st["stage"])

        if st["stage"] == "ask_broker":
            q = (user_msg or "").strip()
            if not q:
                return pack_response(cid, ["Please provide the Stock Broker name."], stage=st["stage"])

            if st.get("choice_mode") == "broker" and st.get("choices"):
                if q.isdigit():
                    idx = int(q) - 1
                    opts = st["choices"]
                    if 0 <= idx < len(opts):
                        picked = opts[idx]
                        st["pending_broker"] = picked
                        st.pop("choice_mode", None); st.pop("choices", None)
                        st["stage"] = "confirm_broker"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"✅ You selected **{picked}**.\nIs this correct? (yes / no)"], stage=st["stage"])
                    if idx == len(opts):
                        st.pop("choice_mode", None); st.pop("choices", None)
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, ["Okay. Please type the **Stock Broker** name again."], stage=st["stage"])

                ok, canon = validate_broker(q)
                if ok:
                    st["pending_broker"] = canon
                    st.pop("choice_mode", None); st.pop("choices", None)
                    st["stage"] = "confirm_broker"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, [f"✅ You typed **{canon}**.\nIs this correct? (yes / no)"], stage=st["stage"])

                menu = _render_choices(st["choices"])
                return pack_response(cid, [f"❓ Did you mean one of these brokers? Choose by number:\n\n{menu}"], stage=st["stage"])

            exact = broker_candidates(q, k=1)
            if len(exact) == 1 and exact[0].lower() == q.lower():
                st["pending_broker"] = exact[0]
                st["stage"] = "confirm_broker"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"🔎 Found exact match: **{exact[0]}**. Is this the correct broker? (yes / no)"], stage=st["stage"])

            options = broker_candidates(q, k=8)
            if options:
                st["choice_mode"] = "broker"
                st["choices"] = options
                session_mgr.update_session(cid, st)
                menu = _render_choices(options)
                return pack_response(cid, [f"❓ Did you mean one of these brokers? Choose by number:\n\n{menu}"], stage=st["stage"])

            return pack_response(cid, ["❌ That broker is not in the registered list. Please provide a **registered broker** name."], stage=st["stage"])

        if st["stage"] == "confirm_exchange":
            low = (user_msg or "").strip().lower()
            if low in YES and st.get("pending_exchange"):
                st["details"]["exchange_name"] = st.pop("pending_exchange")
                if _should_ask_client_dp(st):
                    return _goto_client_dp(st, cid)
                st["stage"] = "waiting_file"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"✅ Exchange confirmed: **{st['details']['exchange_name']}**\n\nIf you have a supporting file, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])
            if low in NO:
                st.pop("pending_exchange", None)
                st["stage"] = "ask_exchange"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["Okay. Please type the **Stock Exchange** (e.g., NSE/BSE or full name)."], stage=st["stage"])
            return pack_response(cid, ["Please reply with 'yes' or 'no'."], stage=st["stage"])

        if st["stage"] == "ask_exchange":
            q = (user_msg or "").strip()
            if not q:
                return pack_response(cid, ["Please provide the Stock Exchange (e.g., NSE/BSE or full name)."], stage=st["stage"])

            if st.get("choice_mode") == "exchange" and st.get("choices"):
                if q.isdigit():
                    idx = int(q) - 1
                    opts = st["choices"]
                    if 0 <= idx < len(opts):
                        picked = opts[idx]
                        st["pending_exchange"] = picked
                        st.pop("choice_mode", None); st.pop("choices", None)
                        st["stage"] = "confirm_exchange"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"✅ You selected **{picked}**.\nIs this correct? (yes / no)"], stage=st["stage"])
                    if idx == len(opts):
                        st.pop("choice_mode", None); st.pop("choices", None)
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, ["Okay. Please type the **Stock Exchange** again."], stage=st["stage"])

                ok, canon = validate_exchange(q)
                if ok:
                    st["pending_exchange"] = canon
                    st.pop("choice_mode", None); st.pop("choices", None)
                    st["stage"] = "confirm_exchange"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, [f"✅ You typed **{canon}**.\nIs this correct? (yes / no)"], stage=st["stage"])

                menu = _render_choices(st["choices"])
                return pack_response(cid, [f"❓ Did you mean one of these exchanges? Choose by number:\n\n{menu}"], stage=st["stage"])

            ok, canon = validate_exchange(q)
            if ok:
                st["pending_exchange"] = canon
                st["stage"] = "confirm_exchange"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"🔎 I found **{canon}**. Is this the correct exchange? (yes / no)"], stage=st["stage"])

            options = exchange_suggestions(q, k=6)
            if options:
                st["choice_mode"] = "exchange"
                st["choices"] = options
                session_mgr.update_session(cid, st)
                menu = _render_choices(options)
                return pack_response(cid, [f"❓ Did you mean one of these exchanges? Choose by number:\n\n{menu}"], stage=st["stage"])

            return pack_response(cid, ["❌ That exchange is not recognized. Please provide a valid exchange (e.g., NSE/BSE or full name)."], stage=st["stage"])

        if st["stage"] == "ask_client_dp":
            q = (user_msg or "").strip()
            if q.lower() in {"no", "skip"}:
                st["details"]["client_or_dp"] = ""
                st["stage"] = "waiting_file"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["Okay, skipping Client/DP ID.\nIf you have a supporting file, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])

            if not CLIENT_OR_DP_RE.match(q):
                return pack_response(cid, ["That doesn't look like a valid **Client/DP ID**. Use 5–25 characters (letters/digits/-_/.). Re-enter or type 'no' to skip."], stage=st["stage"])

            st["details"]["client_or_dp"] = q
            st["stage"] = "waiting_file"
            session_mgr.update_session(cid, st)
            return pack_response(cid, ["✅ Noted.\nIf you have a supporting file, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])

        # ===================== LISTED COMPANY — EQUITY ISSUE FLOW =====================
        if st["stage"] == "confirm_company":
            low = (user_msg or "").strip().lower()
            if low in YES and st.get("pending_company"):
                st["details"]["company_name"] = st.pop("pending_company")
                st["stage"] = "ask_holding_mode"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"✅ Company confirmed: **{st['details']['company_name']}**\n\nIs your holding **Physical** or **Demat**?"], stage=st["stage"])
            if low in NO:
                st.pop("pending_company", None)
                st["stage"] = "ask_company"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["Okay. Please type the **Listed Company** name."], stage=st["stage"])
            return pack_response(cid, ["Please reply with 'yes' or 'no'."], stage=st["stage"])

        if st["stage"] == "ask_company":
            q = (user_msg or "").strip()
            if not q:
                return pack_response(cid, ["Please provide the **Listed Company** name."], stage=st["stage"])

            if st.get("choice_mode") == "company" and st.get("choices"):
                if q.isdigit():
                    idx = int(q) - 1
                    opts = st["choices"]
                    if 0 <= idx < len(opts):
                        picked = opts[idx]
                        st["pending_company"] = picked
                        st.pop("choice_mode", None); st.pop("choices", None)
                        st["stage"] = "confirm_company"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"✅ You selected **{picked}**.\nIs this correct? (yes / no)"], stage=st["stage"])
                    if idx == len(opts):
                        st.pop("choice_mode", None); st.pop("choices", None)
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, ["Okay. Please type the **Listed Company** name again."], stage=st["stage"])

                ok, canon = validate_company(q)
                if ok:
                    st["pending_company"] = canon
                    st.pop("choice_mode", None); st.pop("choices", None)
                    st["stage"] = "confirm_company"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, [f"✅ You typed **{canon}**.\nIs this correct? (yes / no)"], stage=st["stage"])

                menu = _render_choices(st["choices"])
                return pack_response(cid, [f"❓ Did you mean one of these companies? Choose by number:\n\n{menu}"], stage=st["stage"])

            options = company_candidates(q, k=8)
            if options and not (len(options) == 1 and options[0].lower() == q.lower()):
                st["choice_mode"] = "company"
                st["choices"] = options
                session_mgr.update_session(cid, st)
                menu = _render_choices(options)
                return pack_response(cid, [f"❓ Did you mean one of these companies? Choose by number:\n\n{menu}"], stage=st["stage"])

            ok, canon = validate_company(q)
            if ok:
                st["pending_company"] = canon
                st["stage"] = "confirm_company"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"🔎 I found **{canon}**. Is this the correct company? (yes / no)"], stage=st["stage"])

            return pack_response(cid, ["❌ That company is not in the registered list. Please provide a **registered company** name."], stage=st["stage"])

        if st["stage"] == "ask_holding_mode":
            q = (user_msg or "").strip().lower()
            if q in {"physical", "p"}:
                st["details"]["holding_mode"] = "Physical"
                st["stage"] = "ask_folio"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["🧾 Please enter your **Folio Number** (5–25 chars, letters/digits/-_/.)"], stage=st["stage"])
            if q in {"demat", "d"}:
                st["details"]["holding_mode"] = "Demat"
                st["stage"] = "ask_demat_acct"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["🧾 Please enter your **Demat Account Number / DP–Client ID** (7–24 chars)."], stage=st["stage"])
            return pack_response(cid, ["Please type **Physical** or **Demat**."], stage=st["stage"])

        if st["stage"] == "ask_folio":
            q = (user_msg or "").strip()
            if not q or not FOLIO_RE.match(q):
                return pack_response(cid, ["That doesn’t look like a valid **Folio Number**. Use 5–25 characters (letters/digits/-_/.). Please re-enter."], stage=st["stage"])
            st["details"]["folio_number"] = q
            st["stage"] = "waiting_file"
            session_mgr.update_session(cid, st)
            return pack_response(cid, ["✅ Noted.\nIf you have a supporting file, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])

        if st["stage"] == "ask_demat_acct":
            q = (user_msg or "").strip()
            if not q or not DEMAT_ACCT_RE.match(q):
                return pack_response(cid, ["That doesn’t look like a valid **Demat Account / DP–Client ID**. Use 7–24 characters (letters/digits/-_/.). Please re-enter."], stage=st["stage"])
            st["details"]["demat_account_number"] = q
            st["stage"] = "waiting_file"
            session_mgr.update_session(cid, st)
            return pack_response(cid, ["✅ Noted.\nIf you have a supporting file, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])

        # ===================== MUTUAL FUND FLOW =====================
        if st["stage"] == "confirm_mutualfund":
            low = (user_msg or "").strip().lower()
            if low in YES and st.get("pending_mutualfund"):
                st["details"]["mutual_fund_name"] = st.pop("pending_mutualfund")
                st["stage"] = "waiting_file"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"✅ Mutual Fund confirmed: **{st['details']['mutual_fund_name']}**\n\nIf you have a supporting file, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])
            if low in NO:
                st.pop("pending_mutualfund", None)
                st["stage"] = "ask_mutualfund"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["Okay. Please type the **Mutual Fund** name."], stage=st["stage"])
            return pack_response(cid, ["Please reply with 'yes' or 'no'."], stage=st["stage"])

        if st["stage"] == "ask_mutualfund":
            q = (user_msg or "").strip()
            if not q:
                return pack_response(cid, ["Please tell me the **Mutual Fund** name."], stage=st["stage"])

            if st.get("choice_mode") == "mutualfund" and st.get("choices"):
                if q.isdigit():
                    idx = int(q) - 1
                    opts = st["choices"]
                    if 0 <= idx < len(opts):
                        picked = opts[idx]
                        st["pending_mutualfund"] = picked
                        st.pop("choice_mode", None); st.pop("choices", None)
                        st["stage"] = "confirm_mutualfund"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"✅ You selected **{picked}**.\nIs this the correct Mutual Fund? (yes / no)"], stage=st["stage"])
                    if idx == len(opts):
                        st.pop("choice_mode", None); st.pop("choices", None)
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, ["Okay. Please type the **Mutual Fund** name again."], stage=st["stage"])

                ok, canon = validate_mutualfund(q)
                if ok:
                    st["pending_mutualfund"] = canon
                    st.pop("choice_mode", None); st.pop("choices", None)
                    st["stage"] = "confirm_mutualfund"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, [f"✅ You typed **{canon}**.\nIs this the correct Mutual Fund? (yes / no)"], stage=st["stage"])

                menu = _render_choices(st["choices"])
                return pack_response(cid, [f"❓ Did you mean one of these Mutual Funds? Choose by number:\n\n{menu}"], stage=st["stage"])

            exact = mutualfund_candidates(q, k=1)
            if len(exact) == 1 and exact[0].lower() == q.lower():
                st["pending_mutualfund"] = exact[0]
                st["stage"] = "confirm_mutualfund"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"🔎 Found exact match: **{exact[0]}**. Is this the correct Mutual Fund? (yes / no)"], stage=st["stage"])

            options = mutualfund_candidates(q, k=8)
            if options:
                st["choice_mode"] = "mutualfund"
                st["choices"] = options
                session_mgr.update_session(cid, st)
                menu = _render_choices(options)
                return pack_response(cid, [f"❓ Did you mean one of these Mutual Funds? Choose by number:\n\n{menu}"], stage=st["stage"])

            return pack_response(cid, ["❌ That Mutual Fund is not in the list. Please provide a **registered Mutual Fund** name."], stage=st["stage"])

        # ===================== INVESTMENT ADVISER FLOW =====================
        if st["stage"] == "confirm_advisor":
            low = (user_msg or "").strip().lower()
            if low in YES and st.get("pending_advisor"):
                st["details"]["investment_advisor_name"] = st.pop("pending_advisor")
                st["stage"] = "waiting_file"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"✅ Investment Adviser confirmed: **{st['details']['investment_advisor_name']}**\n\nIf you have a supporting file, upload it now. Otherwise, type 'no' to continue."], stage=st["stage"])
            if low in NO:
                st.pop("pending_advisor", None)
                st["stage"] = "ask_advisor"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["Okay. Please type the **Investment Adviser** name."], stage=st["stage"])
            return pack_response(cid, ["Please reply with 'yes' or 'no'."], stage=st["stage"])

        if st["stage"] == "ask_advisor":
            q = (user_msg or "").strip()
            if not q:
                return pack_response(cid, ["Please tell me the **Investment Adviser** name."], stage=st["stage"])

            if st.get("choice_mode") == "advisor" and st.get("choices"):
                if q.isdigit():
                    idx = int(q) - 1
                    opts = st["choices"]
                    if 0 <= idx < len(opts):
                        picked = opts[idx]
                        st["pending_advisor"] = picked
                        st.pop("choice_mode", None); st.pop("choices", None)
                        st["stage"] = "confirm_advisor"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [f"✅ You selected **{picked}**.\nIs this the correct Investment Adviser? (yes / no)"], stage=st["stage"])
                    if idx == len(opts):
                        st.pop("choice_mode", None); st.pop("choices", None)
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, ["Okay. Please type the **Investment Adviser** name again."], stage=st["stage"])

                ok, canon = validate_advisor(q)
                if ok:
                    st["pending_advisor"] = canon
                    st.pop("choice_mode", None); st.pop("choices", None)
                    st["stage"] = "confirm_advisor"
                    session_mgr.update_session(cid, st)
                    return pack_response(cid, [f"✅ You typed **{canon}**.\nIs this the correct Investment Adviser? (yes / no)"], stage=st["stage"])

                menu = _render_choices(st["choices"])
                return pack_response(cid, [f"❓ Did you mean one of these Investment Advisers? Choose by number:\n\n{menu}"], stage=st["stage"])

            exact = advisor_candidates(q, k=1)
            if len(exact) == 1 and exact[0].lower() == q.lower():
                st["pending_advisor"] = exact[0]
                st["stage"] = "confirm_advisor"
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"🔎 Found exact match: **{exact[0]}**. Is this the correct Investment Adviser? (yes / no)"], stage=st["stage"])

            options = advisor_candidates(q, k=8)
            if options:
                st["choice_mode"] = "advisor"
                st["choices"] = options
                session_mgr.update_session(cid, st)
                menu = _render_choices(options)
                return pack_response(cid, [f"❓ Did you mean one of these Investment Advisers? Choose by number:\n\n{menu}"], stage=st["stage"])

            return pack_response(cid, ["❌ That Investment Adviser is not in the list. Please provide a **registered Investment Adviser** name."], stage=st["stage"])

        # ---------- Waiting for optional file ----------
        if st["stage"] == "waiting_file":
            low = (user_msg or "").lower()
            if incoming_path:
                st["attachment_path"] = incoming_path
                file_url = f"/uploads/{os.path.basename(incoming_path)}"
                st["stage"] = "collect_details"
                st["details_step_index"] = 0
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["📎 Attachment received.\n" + ask_current_detail(st)], stage=st["stage"], attachment_url=file_url)

            if low in NO or low == "skip":
                st["stage"] = "collect_details"
                st["details_step_index"] = 0
                session_mgr.update_session(cid, st)
                return pack_response(cid, [ask_current_detail(st)], stage=st["stage"])

            return pack_response(cid, ["Please upload a file now, or type 'no' to continue without it."], stage=st["stage"])

        # ---------- Collect details / Verify OTP / Review / Submit ----------
        if st["stage"] == "collect_details":
            try:
                err = handle_detail_input(st, user_msg)
            except Exception as e:
                log.exception("handle_detail_input error: %s", e)
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["Something went wrong while reading that field. Please try again."], stage=st["stage"])
            if err:
                session_mgr.update_session(cid, st)
                return pack_response(cid, [err], stage=st["stage"])

            if st["stage"] == "verify_otp":
                target = st["otp"].get("target")
                code = st["otp"].get(target, {}).get("code") if target else None
                dev = f" (DEV: {code})" if OTP_DEBUG_SHOW_CODE and code else ""
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"An OTP has been sent to your {target}. Please enter the 6-digit code.{dev}"], stage="verify_otp")

            if st["details_step_index"] < len(DETAIL_STEPS):
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["✅ Noted.\n" + ask_current_detail(st)], stage=st["stage"])

            st["stage"] = "review_confirm"
            session_mgr.update_session(cid, st)
            d = st["details"]
            review = (
                "📋 Please review your complaint:\n"
                + f"📝 Description → {st.get('description')}\n\n"
                + f"📁 Category → {st.get('pred_category')}\n\n"
                + f"📂 Sub-category → {st.get('pred_sub_category')}\n\n"
                + (f"🏢 Broker → {d.get('broker_name')}\n" if d.get("broker_name") else "")
                + (f"🏛 Exchange → {d.get('exchange_name')}\n" if d.get("exchange_name") else "")
                + (f"🧾 Client/DP ID → {d.get('client_or_dp') or '—'}\n" if d.get("broker_name") or d.get("exchange_name") else "")
                + (f"🏢 Company → {d.get('company_name')}\n" if d.get("company_name") else "")
                + (f"📦 Holding → {d.get('holding_mode')}\n" if d.get("holding_mode") else "")
                + (f"🔖 Folio No → {d.get('folio_number')}\n" if d.get("folio_number") else "")
                + (f"💳 Demat A/c → {d.get('demat_account_number')}\n" if d.get("demat_account_number") else "")
                + (f"🏦 Mutual Fund → {d.get('mutual_fund_name')}\n" if d.get("mutual_fund_name") else "")
                + (f"🧑‍💼 Investment Adviser → {d.get('investment_advisor_name')}\n" if d.get("investment_advisor_name") else "")
                + f"👤 Name (as per PAN) → {d.get('full_name','')}\n"
                + f"📞 Phone → {d.get('phone','')}{' ✅' if st['otp']['phone']['verified'] else ''}\n"
                + f"✉️ Email → {d.get('email','')}{' ✅' if st['otp']['email']['verified'] else ''}\n"
                + f"🪪 PAN → {d.get('pan','')}\n"
                + f"🏠 Address → {d.get('address','')}\n"
                + f"🎂 DOB → {d.get('dob','')}\n\n"
                + f"📎 Attachment → {'Yes' if st.get('attachment_path') else 'No'}\n\n"
                + "Do you want to submit this complaint now? (yes / no)"
            )
            return pack_response(
                cid, [review], stage=st["stage"],
                attachment_url=(f"/uploads/{os.path.basename(st['attachment_path'])}" if st.get("attachment_path") else None)
            )

        if st["stage"] == "verify_otp":
            target = st["otp"].get("target")
            if not target:
                if not st["otp"]["phone"]["verified"]:
                    _begin_otp(st, "phone"); target = "phone"
                elif not st["otp"]["email"]["verified"]:
                    _begin_otp(st, "email"); target = "email"
                else:
                    if st["details_step_index"] < len(DETAIL_STEPS):
                        st["stage"] = "collect_details"
                        session_mgr.update_session(cid, st)
                        return pack_response(cid, [ask_current_detail(st)], stage=st["stage"])
                    st["stage"] = "review_confirm"
                    session_mgr.update_session(cid, st)
                    d = st["details"]
                    review = (
                        "📋 Please review your complaint:\n"
                        + f"📝 Description → {st.get('description')}\n\n"
                        + f"📁 Category → {st.get('pred_category')}\n\n"
                        + f"📂 Sub-category → {st.get('pred_sub_category')}\n\n"
                        + (f"🏢 Company → {d.get('company_name')}\n" if d.get("company_name") else "")
                        + (f"📦 Holding → {d.get('holding_mode')}\n" if d.get("holding_mode") else "")
                        + (f"🔖 Folio No → {d.get('folio_number')}\n" if d.get("folio_number") else "")
                        + (f"💳 Demat A/c → {d.get('demat_account_number')}\n" if d.get("demat_account_number") else "")
                        + f"👤 Name (as per PAN) → {d.get('full_name','')}\n"
                        + f"📞 Phone → {d.get('phone','')} ✅\n"
                        + f"✉️ Email → {d.get('email','')} ✅\n"
                        + f"🪪 PAN → {d.get('pan','')}\n"
                        + f"🏠 Address → {d.get('address','')}\n"
                        + f"🎂 DOB → {d.get('dob','')}\n\n"
                        + f"📎 Attachment → {'Yes' if st.get('attachment_path') else 'No'}\n\n"
                        + "Do you want to submit this complaint now? (yes / no)"
                    )
                    return pack_response(
                        cid, [review], stage=st["stage"],
                        attachment_url=(f"/uploads/{os.path.basename(st['attachment_path'])}" if st.get("attachment_path") else None)
                    )

            if (user_msg or "").strip().lower() == "resend":
                _begin_otp(st, target)
                code = st["otp"][target].get("code")
                dev = f" (DEV: {code})" if OTP_DEBUG_SHOW_CODE and code else ""
                session_mgr.update_session(cid, st)
                return pack_response(cid, [f"New OTP sent to your {target}. Please enter the 6-digit code.{dev}"], stage="verify_otp")

            code_in = re.sub(r"\D", "", user_msg or "")
            if len(code_in) != 6:
                code = st["otp"][target].get("code")
                dev = f" (DEV: {code})" if OTP_DEBUG_SHOW_CODE and code else ""
                return pack_response(cid, [f"Please enter the 6-digit OTP code for your {target}.{dev}"], stage="verify_otp")

            err = _check_otp(st, target, code_in)
            if err:
                code = st["otp"][target].get("code")
                dev = f" (DEV: {code})" if OTP_DEBUG_SHOW_CODE and code else ""
                return pack_response(cid, [f"⚠️ {err}{dev}"], stage="verify_otp")

            st["otp"]["target"] = None
            if st["details_step_index"] < len(DETAIL_STEPS):
                current_key = DETAIL_STEPS[st["details_step_index"]][0]
                if current_key in ("phone", "email"):
                    st["details_step_index"] += 1

            if st["details_step_index"] < len(DETAIL_STEPS):
                st["stage"] = "collect_details"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["✅ OTP verified.\n" + ask_current_detail(st)], stage=st["stage"])

            st["stage"] = "review_confirm"
            session_mgr.update_session(cid, st)
            d = st["details"]
            review = (
                "📋 Please review your complaint:\n"
                + f"📝 Description → {st.get('description')}\n\n"
                + f"📁 Category → {st.get('pred_category')}\n\n"
                + f"📂 Sub-category → {st.get('pred_sub_category')}\n\n"
                + (f"🏢 Company → {d.get('company_name')}\n" if d.get("company_name") else "")
                + (f"📦 Holding → {d.get('holding_mode')}\n" if d.get("holding_mode") else "")
                + (f"🔖 Folio No → {d.get('folio_number')}\n" if d.get("folio_number") else "")
                + (f"💳 Demat A/c → {d.get('demat_account_number')}\n" if d.get("demat_account_number") else "")
                + f"👤 Name (as per PAN) → {d.get('full_name','')}\n"
                + f"📞 Phone → {d.get('phone','')} ✅\n"
                + f"✉️ Email → {d.get('email','')} ✅\n"
                + f"🪪 PAN → {d.get('pan','')}\n"
                + f"🏠 Address → {d.get('address','')}\n"
                + f"🎂 DOB → {d.get('dob','')}\n\n"
                + f"📎 Attachment → {'Yes' if st.get('attachment_path') else 'No'}\n\n"
                + "Do you want to submit this complaint now? (yes / no)"
            )
            return pack_response(
                cid, [review], stage=st["stage"],
                attachment_url=(f"/uploads/{os.path.basename(st['attachment_path'])}" if st.get("attachment_path") else None)
            )

        if st["stage"] == "review_confirm":
            low = (user_msg or "").lower()
            if low in YES:
                desc = st["description"] or ""
                cat  = st["pred_category"] or ""
                sub  = st["pred_sub_category"] or ""
                attachment_path = st.get("attachment_path")

                if not st["otp"]["phone"]["verified"]:
                    _begin_otp(st, "phone")
                    st["stage"] = "verify_otp"
                    session_mgr.update_session(cid, st)
                    code = st["otp"]["phone"].get("code")
                    dev = f" (DEV: {code})" if OTP_DEBUG_SHOW_CODE and code else ""
                    return pack_response(cid, [f"We need to verify your phone before submission. Enter the 6-digit OTP.{dev}"], stage="verify_otp")
                if not st["otp"]["email"]["verified"]:
                    _begin_otp(st, "email")
                    st["stage"] = "verify_otp"
                    session_mgr.update_session(cid, st)
                    code = st["otp"]["email"].get("code")
                    dev = f" (DEV: {code})" if OTP_DEBUG_SHOW_CODE and code else ""
                    return pack_response(cid, [f"We need to verify your email before submission. Enter the 6-digit OTP.{dev}"], stage="verify_otp")

                cmp_no = lodge_complaint(desc, cat, sub, attachment_path, st["details"])
                if all([desc, cat, sub]):
                    append_to_csv(desc, cat, sub)

                st["stage"] = "completed"
                session_mgr.update_session(cid, st)

                summary = (
                    "✅ Complaint submitted successfully!\n"
                    + f"🔢 Complaint Number → {cmp_no}\n\n"
                    + "Need to raise another complaint?\n"
                    + "Just type or say 'start'. Say 'done' to end."
                )
                if attachment_path:
                    return pack_response(
                        cid,
                        [summary],
                        stage="completed",
                        complaint_number=cmp_no,
                        attachment_url=f"/uploads/{os.path.basename(attachment_path)}"
                    )
                return pack_response(cid, [summary], stage="completed", complaint_number=cmp_no)

            if low in NO:
                st["stage"] = "waiting_file"
                session_mgr.update_session(cid, st)
                return pack_response(cid, ["No problem. You can upload a different file now, or type 'no' to continue without it."], stage=st["stage"])

            return pack_response(cid, ["Please reply with 'yes' or 'no'."], stage=st["stage"])

        # ---------- Fallback ----------
        return pack_response(cid, ["Please describe your complaint (you can attach a file too)."], stage=st["stage"])

    except Exception as e:
        log.exception("Unhandled error in /chat: %s", e)
        try:
            cid = cid if 'cid' in locals() else ""
            st = st if 'st' in locals() else {}
            session_mgr.update_session(cid, st)
        except Exception:
            pass
        return jsonify({
            "cid": (cid if 'cid' in locals() else None),
            "messages": ["⚠️ Something went wrong. Please send that again."],
            "response": "⚠️ Something went wrong. Please send that again.",
            "stage": st["stage"] if 'st' in locals() and isinstance(st, dict) and st.get("stage") else "awaiting_description"
        }), 200

# ---------- Run ----------
if __name__ == "__main__":
    log.info("Extractors available: %s", ", ".join(EXTRACTORS_INFO) or "none")
    app.run(host="0.0.0.0", port=5000, debug=True)
