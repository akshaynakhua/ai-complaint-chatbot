import re
from datetime import date
from typing import Optional

PHONE_RE = re.compile(r"^\+?\d{10,14}$")
EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)
PAN_RE   = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$", re.I)

CLIENT_OR_DP_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{4,24}$", re.I)
FOLIO_RE        = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{4,24}$", re.I)
DEMAT_ACCT_RE   = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{6,24}$", re.I)

DETAIL_STEPS = [
    ("full_name", "👤 Please enter your Full Name (as per PAN):"),
    ("phone",     "📞 Please enter your Phone number:"),
    ("email",     "✉️ Please enter your Email ID:"),
    ("pan",       "🪪 Please enter your PAN (e.g., ABCDE1234F):"),
    ("address",   "🏠 Please enter your Address:"),
    ("dob",       "🎂 Please enter your Date of Birth (YYYY-MM-DD or DD/MM/YYYY):"),
]

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

def ask_current_detail(st) -> str:
    key, prompt = DETAIL_STEPS[st["details_step_index"]]
    return prompt

def handle_detail_input(st, user_text: str) -> str | None:
    key, _prompt = DETAIL_STEPS[st["details_step_index"]]
    val = (user_text or "").strip()

    if key == "full_name":
        if len(val) < 2: return "Name looks too short. Please enter your Full Name (as per PAN):"
        st["details"]["full_name"] = val; st["details_step_index"] += 1; return None

    if key == "phone":
        if not PHONE_RE.match(val): return "Please enter a valid phone number, e.g. +9198XXXXXXXX or 98XXXXXXXX."
        st["details"]["phone"] = val; st["stage"] = "verify_otp"; return None

    if key == "email":
        if not EMAIL_RE.match(val): return "Please enter a valid Email ID (e.g., name@example.com):"
        st["details"]["email"] = val; st["stage"] = "verify_otp"; return None

    if key == "pan":
        if not PAN_RE.match(val): return "PAN looks invalid. Please enter like ABCDE1234F:"
        st["details"]["pan"] = val.upper(); st["details_step_index"] += 1; return None

    if key == "address":
        if len(val) < 5: return "Address looks too short. Please enter your Address:"
        st["details"]["address"] = val; st["details_step_index"] += 1; return None

    if key == "dob":
        norm = normalize_dob(val)
        if not norm: return "DOB looks invalid. Use YYYY-MM-DD or DD/MM/YYYY.\nPlease enter your Date of Birth:"
        if age_years(norm) < 18: return "DOB looks too recent. You must be at least 18 years old.\nPlease enter your Date of Birth:"
        st["details"]["dob"] = norm; st["details_step_index"] += 1; return None

    return "Unexpected field."
