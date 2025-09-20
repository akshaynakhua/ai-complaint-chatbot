# backend/api/chat.py
from __future__ import annotations

import re, uuid, time, random
from datetime import datetime, date
from typing import Dict, List, Tuple, Optional

from flask import Blueprint, request, jsonify

# ---------- Session manager ----------
try:
    from session_handler import SessionManager
    session_mgr = SessionManager()
except Exception:
    _SESS: Dict[str, Dict] = {}
    class _SM:
        def ensure_session_id(self, cid: str) -> str: return cid or uuid.uuid4().hex
        def get_session(self, cid: str) -> Dict: return _SESS.setdefault(cid, {})
        def update_session(self, cid: str, st: Dict): _SESS[cid] = st
        def reset_session(self, cid: str) -> Dict: _SESS[cid] = {}; return _SESS[cid]
    session_mgr = _SM()

# ---------- Services ----------
import services.registries_service as reg
from services import tone
from services import smalltalk

# ---------- Blueprint ----------
bp = Blueprint("chat_api", __name__)

# ---------- Regex / utils ----------
GREETING_RE = re.compile(r"^(hi|hello|hey|namaste|yo|good\s*(morning|evening|afternoon))[\W_]*$", re.I)

def is_greeting(s: str) -> bool:
    return bool(GREETING_RE.match((s or "").strip()))

GENERIC_ACK = {
    "ok","okay","k","kk","cool","great","fine","sure","yep","yup","done","wait","one sec","one second",
    "thanks","thank you","ty","hmm","hmmm","h","alright","right","got it","noted"
}
def is_generic_ack(s: str) -> bool:
    s = (s or "").strip().lower()
    if not s: return True
    if s in GENERIC_ACK: return True
    return len(s.split()) <= 2 and s.replace(".", "").replace("!", "") in GENERIC_ACK

# Domain-ish words – helps decide if the message is a real complaint line
DOMAIN_HINTS = {
    "broker","stock","nse","bse","exchange","order","ipo","payout","margin","pledge",
    "mutual","mf","fund","nav","allot","redemption","units","sip","folio",
    "company","dividend","transmission","duplicate","share","demat","dp","client id",
    "adviser","advisor","investment","ria","portfolio","account","suspension","kyc"
}
def looks_like_complaint_line(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t or is_greeting(t) or is_generic_ack(t): return False
    # if it has at least one domain hint or is reasonably descriptive
    return any(w in t for w in DOMAIN_HINTS) or len(t.split()) >= 5

def clean_text(t: str) -> str:
    return re.sub(r"[ \t]+", " ", (t or "")).strip()

def pack_response(cid, messages: List[str], **extra):
    return jsonify({"cid": cid, "messages": messages, "response": messages[0] if messages else "", **extra})

# ---------- OTP / Validators ----------
PAN_RE   = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$", re.I)
PHONE_RE = re.compile(r"^\+?\d{10,14}$")
EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)
CLIENT_OR_DP_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{4,24}$", re.I)
FOLIO_RE       = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{4,24}$", re.I)
DEMAT_ACCT_RE  = re.compile(r"^[A-Z0-9][A-Z0-9\-_/\.]{6,24}$", re.I)

def normalize_dob(s: str) -> Optional[str]:
    s = (s or "").strip()
    m1 = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    m2 = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
    try:
        if m1: y, mo, d = map(int, m1.groups())
        elif m2: d, mo, y = map(int, m2.groups())
        else: return None
        return date(y, mo, d).isoformat()
    except Exception:
        return None

def age_years(iso_date: str) -> int:
    y, m, d = map(int, iso_date.split("-"))
    dob = date(y, m, d); today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

def _gen_otp() -> str: return f"{random.randint(0, 999999):06d}"

YES = {"yes","y","ok","okay","confirm","confirmed"}
NO  = {"no","n","nah","nope"}

# NEW: phrases that should end the session after submission
CLOSE_PHRASES = {
    "done","bye","goodbye","exit","quit","finish","finished",
    "no thank you","no thanks","thanks","thank you","close","end"
}

# ---------- States ----------
STATE = {
    "AWAIT_DESC": "awaiting_description",
    "CONFIRMING": "confirming",
    "CONFIRM_BROKER": "confirm_broker",
    "ASK_BROKER": "ask_broker",
    "CONFIRM_EXCHANGE": "confirm_exchange",
    "ASK_EXCHANGE": "ask_exchange",
    "CONFIRM_COMPANY": "confirm_company",
    "ASK_COMPANY": "ask_company",
    "ASK_HOLDING": "ask_holding_mode",
    "ASK_FOLIO": "ask_folio",
    "ASK_DEMAT": "ask_demat_acct",
    "CONFIRM_MF": "confirm_mutualfund",
    "ASK_MF": "ask_mutualfund",
    "CONFIRM_ADVISER": "confirm_advisor",
    "ASK_ADVISER": "ask_advisor",
    "WAIT_FILE": "waiting_file",
    "COLLECT": "collect_details",
    "VERIFY_OTP": "verify_otp",
    "REVIEW": "review_confirm",
    "COMPLETED": "completed",
    "ENDED": "ended",
}

DETAIL_STEPS = [
    ("full_name", "👤 Please enter your **Full Name** (as per PAN):"),
    ("phone",     "📞 Please enter your **Phone number**:"),
    ("email",     "✉️ Please enter your **Email ID**:"),
    ("pan",       "🪪 Please enter your **PAN** (e.g., ABCDE1234F):"),
    ("address",   "🏠 Please enter your **Address**:"),
    ("dob",       "🎂 Please enter your **Date of Birth** (YYYY-MM-DD or DD/MM/YYYY):"),
]
def ask_current_detail(st) -> str: return DETAIL_STEPS[st["details_step_index"]][1]

# ---------- Session helpers ----------
def _get_cid_and_state() -> Tuple[str, dict]:
    ctype = (request.content_type or "").lower()
    if "multipart/form-data" in ctype:
        cid = (request.form.get("cid") or "").strip()
    else:
        data = request.get_json(silent=True) or {}; cid = (data.get("cid") or "").strip()
    cid = session_mgr.ensure_session_id(cid); st = session_mgr.get_session(cid); return cid, st

def _ensure_defaults(st: Dict):
    st.setdefault("stage", STATE["AWAIT_DESC"])
    st.setdefault("details", {})
    st.setdefault("details_step_index", 0)
    st.setdefault("otp", {"target": None, "phone": {"code": None, "ts": 0, "verified": False}, "email": {"code": None, "ts": 0, "verified": False}})
    st.setdefault("OTP_DEBUG_SHOW_CODE", True)

# ---------- Candidate helpers ----------
def _seed_entity_candidates(st: Dict):
    cat = (st.get("pred_category") or "").lower()
    desc = st.get("description") or ""
    if cat == "stock broker":
        b, e = reg.detect_broker_and_exchange_from_text(desc) or (None, None)
        if b and not st["details"].get("broker_name") and not st.get("pending_broker"): st["pending_broker"] = b
        if e and not st["details"].get("exchange_name") and not st.get("pending_exchange"): st["pending_exchange"] = e
    if "listed" in cat:
        hits = reg.company_candidates(desc) or []
        if hits and not st["details"].get("company_name") and not st.get("pending_company"): st["pending_company"] = hits[0]
    if "mutual fund" in cat:
        hits = reg.mutualfund_candidates(desc) or []
        if hits and not st["details"].get("mutual_fund_name") and not st.get("pending_mutualfund"): st["pending_mutualfund"] = hits[0]
    if "advis" in cat:
        hits = reg.advisor_candidates(desc) or []
        if hits and not st["details"].get("investment_advisor_name") and not st.get("pending_advisor"): st["pending_advisor"] = hits[0]

def _branch_into_category_flow(cid, st):
    cat = (st.get("pred_category") or "").strip().lower()
    if cat == "stock broker":
        if st.get("pending_broker"):
            st["stage"] = STATE["CONFIRM_BROKER"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("confirm_detected", label="broker", value=st["pending_broker"])], stage=st["stage"])
        st["stage"] = STATE["ASK_BROKER"]; session_mgr.update_session(cid, st)
        return pack_response(cid, [tone.say("ask_entity", label="broker")], stage=st["stage"])
    if "listed" in cat:
        if st.get("pending_company"):
            st["stage"] = STATE["CONFIRM_COMPANY"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("confirm_detected", label="company", value=st["pending_company"])], stage=st["stage"])
        st["stage"] = STATE["ASK_COMPANY"]; session_mgr.update_session(cid, st)
        return pack_response(cid, [tone.say("ask_entity", label="company")], stage=st["stage"])
    if "mutual fund" in cat:
        if st.get("pending_mutualfund"):
            st["stage"] = STATE["CONFIRM_MF"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("confirm_detected", label="mutualfund", value=st["pending_mutualfund"])], stage=st["stage"])
        st["stage"] = STATE["ASK_MF"]; session_mgr.update_session(cid, st)
        return pack_response(cid, [tone.say("ask_entity", label="mutualfund")], stage=st["stage"])
    if "advis" in cat:
        if st.get("pending_advisor"):
            st["stage"] = STATE["CONFIRM_ADVISER"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("confirm_detected", label="advisor", value=st["pending_advisor"])], stage=st["stage"])
        st["stage"] = STATE["ASK_ADVISER"]; session_mgr.update_session(cid, st)
        return pack_response(cid, [tone.say("ask_entity", label="advisor")], stage=st["stage"])
    st["stage"] = STATE["WAIT_FILE"]; session_mgr.update_session(cid, st)
    return pack_response(cid, [tone.say("file_or_skip")], stage=st["stage"])

# ---------- Menu helpers ----------
def _render_choices(options: List[str]) -> str:
    lines = [f"{i+1}) {opt}" for i, opt in enumerate(options)]
    lines.append(f"{len(options)+1}) None of these")
    return "\n".join(lines)

def _choice_pick(cid, st, q: str, mode: str, confirm_stage: str, reask_prompt: str):
    if not (q.isdigit() and st.get("choice_mode") == mode and st.get("choices")): return None
    idx = int(q) - 1; opts = st["choices"]
    if 0 <= idx < len(opts):
        picked = opts[idx]
        if mode == "broker": st["pending_broker"] = picked
        elif mode == "exchange": st["pending_exchange"] = picked
        elif mode == "company": st["pending_company"] = picked
        elif mode == "mutualfund": st["pending_mutualfund"] = picked
        elif mode == "advisor": st["pending_advisor"] = picked
        st.pop("choice_mode", None); st.pop("choices", None)
        st["stage"] = confirm_stage; session_mgr.update_session(cid, st)
        return pack_response(cid, [f"✅ You selected **{picked}**.\n{tone.say('yes_no_prompt')}"], stage=st["stage"])
    if idx == len(opts):
        st.pop("choice_mode", None); st.pop("choices", None)
        session_mgr.update_session(cid, st)
        return pack_response(cid, [reask_prompt], stage=st["stage"])
    return pack_response(cid, [tone.say("menu_choose", label=mode, menu=_render_choices(opts))], stage=st["stage"])

# ---------- helpers for OTP / Details ----------
def _begin_otp(st, target: str):
    st["otp"]["target"] = target
    st["otp"][target]["code"] = _gen_otp()
    st["otp"][target]["ts"] = time.time()
    st["otp"][target]["verified"] = False

def _check_otp(st, target: str, code: str) -> Optional[str]:
    data = st["otp"][target]
    if not data["code"]: return "No OTP in progress. Type **resend** to get a new OTP."
    if time.time() - data["ts"] > 300: return "OTP expired. Type **resend** to get a new OTP."
    if code != data["code"]: return "Incorrect OTP. Try again or type **resend**."
    data["verified"] = True; data["code"] = None; data["ts"] = 0
    return None

def _handle_detail_input(st, user_text: str) -> Optional[str]:
    key, _ = DETAIL_STEPS[st["details_step_index"]]
    val = (user_text or "").strip()

    if key == "full_name":
        if len(val) < 2: return "Name looks too short. Please enter your **Full Name** (as per PAN):"
        st["details"]["full_name"] = val; st["details_step_index"] += 1; return None

    if key == "phone":
        if not PHONE_RE.match(val): return "Please enter a valid **Phone number**, e.g. +9198XXXXXXXX or 98XXXXXXXX."
        st["details"]["phone"] = val; _begin_otp(st, "phone"); st["stage"] = STATE["VERIFY_OTP"]; return None

    if key == "email":
        if not EMAIL_RE.match(val): return "Please enter a valid **Email ID** (e.g., name@example.com):"
        st["details"]["email"] = val; _begin_otp(st, "email"); st["stage"] = STATE["VERIFY_OTP"]; return None

    if key == "pan":
        if not PAN_RE.match(val): return "PAN looks invalid. Please enter like **ABCDE1234F**:"
        st["details"]["pan"] = val.upper(); st["details_step_index"] += 1; return None

    if key == "address":
        if len(val) < 5: return "Address looks too short. Please enter your **Address**:"
        st["details"]["address"] = val; st["details_step_index"] += 1; return None

    if key == "dob":
        norm = normalize_dob(val)
        if not norm: return "DOB looks invalid. Use **YYYY-MM-DD** or **DD/MM/YYYY**.\nPlease enter your **Date of Birth**:"
        if age_years(norm) < 18: return "You must be at least **18**. Please enter a valid **Date of Birth**:"
        st["details"]["dob"] = norm; st["details_step_index"] += 1; return None

    return "Unexpected field."

def _build_review_text(st) -> str:
    d = st["details"]; cat = st.get('pred_category'); sub = st.get('pred_sub_category')
    lines = [tone.say("review_intro"), f"📝 Description → {st.get('description')}", ""]
    if cat: lines.append(f"📁 Category → {cat}")
    if sub: lines.append(f"📂 Sub-category → {sub}")
    if d.get("broker_name"):             lines.append(f"🏢 Broker → {d['broker_name']}")
    if d.get("exchange_name"):           lines.append(f"🏛 Exchange → {d['exchange_name']}")
    if d.get("company_name"):            lines.append(f"🏢 Company → {d['company_name']}")
    if d.get("holding_mode"):            lines.append(f"📦 Holding → {d['holding_mode']}")
    if d.get("folio_number"):            lines.append(f"🔖 Folio No → {d['folio_number']}")
    if d.get("demat_account_number"):    lines.append(f"💳 Demat A/c → {d['demat_account_number']}")
    if d.get("mutual_fund_name"):        lines.append(f"🏦 Mutual Fund → {d['mutual_fund_name']}")
    if d.get("investment_advisor_name"): lines.append(f"🧑‍💼 Investment Adviser → {d['investment_advisor_name']}")
    lines += [
        f"👤 Name (PAN) → {d.get('full_name','')}",
        f"📞 Phone → {d.get('phone','')}{' ✅' if st['otp']['phone']['verified'] else ''}",
        f"✉️ Email → {d.get('email','')}{' ✅' if st['otp']['email']['verified'] else ''}",
        f"🪪 PAN → {d.get('pan','')}",
        f"🏠 Address → {d.get('address','')}",
        f"🎂 DOB → {d.get('dob','')}",
        f"📎 Attachment → {'Yes' if st.get('attachment_path') else 'No'}",
        "",
        "Do you want to submit this complaint now? (yes / no)"
    ]
    return "\n".join(lines)

# ---------- ROUTE ----------
@bp.post("/chat")
def chat():
    cid, st = _get_cid_and_state(); _ensure_defaults(st)

    ctype = (request.content_type or "").lower()
    if "multipart/form-data" in ctype: user_msg = (request.form.get("message") or "").strip()
    else: data = request.get_json(silent=True) or {}; user_msg = (data.get("message") or "").strip()
    low = (user_msg or "").strip().lower()

    # --- Completed / Ended (handle FIRST so 'done' actually ends) ---
    if st["stage"] in (STATE["COMPLETED"], STATE["ENDED"]):
        if low == "start":
            st = session_mgr.reset_session(cid); _ensure_defaults(st); session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("greet")], stage=st["stage"])
        if low in CLOSE_PHRASES or low in NO:
            st["stage"] = STATE["ENDED"]; session_mgr.update_session(cid, st)
            return pack_response(cid, ["🙏 Thank you. Your session is now closed."], stage=st["stage"])
        # gentle nudge
        if st["stage"] == STATE["COMPLETED"]:
            return pack_response(cid, ["Need to raise another complaint? Type 'start'. Say 'done' to end."], stage=st["stage"])
        return pack_response(cid, ["Session is closed. Type 'start' to raise a new complaint."], stage=st["stage"])

    # --- Small-talk interleave (never changes the stage) ---
    sm = smalltalk.maybe(user_msg)
    if sm:
        # If we’re still waiting for the description, do NOT repeat the big greeting.
        msgs = [sm]
        if st["stage"] == STATE["AWAIT_DESC"]:
            msgs.append(tone.say("ask_more_detail"))
        return pack_response(cid, msgs, stage=st["stage"])

    # ===================== 1) Awaiting description =====================
    if st["stage"] == STATE["AWAIT_DESC"]:
        if not looks_like_complaint_line(user_msg):
            # greet for pure greetings, otherwise prompt for details
            return pack_response(cid, [tone.say("greet") if is_greeting(user_msg) else tone.say("ask_more_detail")], stage=st["stage"])

        st["description"] = clean_text(user_msg)
        try: cat, sub = reg.predict_both(st["description"])
        except Exception: cat, sub = (None, None)
        st["pred_category"], st["pred_sub_category"] = cat, sub

        if not cat and not sub:
            return pack_response(cid, [tone.say("ask_more_detail")], stage=st["stage"])

        st["stage"] = STATE["CONFIRMING"]; _seed_entity_candidates(st); session_mgr.update_session(cid, st)
        return pack_response(cid, [tone.say("confirm_guess", cat=cat, sub=sub)], stage=st["stage"])

    # ===================== 2) Confirming prediction =====================
    if st["stage"] == STATE["CONFIRMING"]:
        if low in YES: return _branch_into_category_flow(cid, st)
        if low in NO:
            st.update({"pred_category": None, "pred_sub_category": None, "description": None})
            st["stage"] = STATE["AWAIT_DESC"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("ask_more_detail")], stage=st["stage"])
        return pack_response(cid, [tone.say("yes_no_prompt")], stage=st["stage"])

    # ===================== 3) Stock Broker flow =====================
    if st["stage"] == STATE["CONFIRM_BROKER"]:
        if low in YES and st.get("pending_broker"):
            st["details"]["broker_name"] = st.pop("pending_broker")
            if st.get("pending_exchange"):
                st["stage"] = STATE["CONFIRM_EXCHANGE"]; session_mgr.update_session(cid, st)
                return pack_response(cid, [tone.say("confirm_detected", label="exchange", value=st["pending_exchange"])], stage=st["stage"])
            st["stage"] = STATE["ASK_EXCHANGE"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("ask_entity", label="exchange")], stage=st["stage"])
        if low in NO:
            st.pop("pending_broker", None); st["stage"] = STATE["ASK_BROKER"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("ask_entity", label="broker")], stage=st["stage"])
        return pack_response(cid, [tone.say("yes_no_prompt")], stage=st["stage"])

    if st["stage"] == STATE["ASK_BROKER"]:
        q = (user_msg or "").strip()
        resp = _choice_pick(cid, st, q, mode="broker", confirm_stage=STATE["CONFIRM_BROKER"], reask_prompt=tone.say("ask_entity", label="broker"))
        if resp: return resp
        ok, canon = reg.validate_broker(q)
        if ok:
            st["pending_broker"] = canon; st["stage"] = STATE["CONFIRM_BROKER"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [f"🔎 Found **{canon}**. {tone.say('yes_no_prompt')}"], stage=st["stage"])
        opts = reg.broker_candidates(q) or []
        if opts:
            st["choice_mode"] = "broker"; st["choices"] = opts; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("menu_choose", label="broker", menu=_render_choices(opts))], stage=st["stage"])
        return pack_response(cid, ["❌ That broker is not in the registered list. Please provide a **registered broker** name."], stage=st["stage"])

    if st["stage"] == STATE["CONFIRM_EXCHANGE"]:
        if low in YES and st.get("pending_exchange"):
            st["details"]["exchange_name"] = st.pop("pending_exchange")
            st["stage"] = STATE["WAIT_FILE"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [f"✅ Exchange confirmed: **{st['details']['exchange_name']}**\n\n{tone.say('file_or_skip')}"], stage=st["stage"])
        if low in NO:
            st.pop("pending_exchange", None); st["stage"] = STATE["ASK_EXCHANGE"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("ask_entity", label="exchange")], stage=st["stage"])
        return pack_response(cid, [tone.say("yes_no_prompt")], stage=st["stage"])

    if st["stage"] == STATE["ASK_EXCHANGE"]:
        q = (user_msg or "").strip()
        resp = _choice_pick(cid, st, q, mode="exchange", confirm_stage=STATE["CONFIRM_EXCHANGE"], reask_prompt=tone.say("ask_entity", label="exchange"))
        if resp: return resp
        ok, canon = reg.validate_exchange(q)
        if ok:
            st["pending_exchange"] = canon; st["stage"] = STATE["CONFIRM_EXCHANGE"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [f"🔎 Found **{canon}**. {tone.say('yes_no_prompt')}"], stage=st["stage"])
        opts = reg.exchange_suggestions(q) or []
        if opts:
            st["choice_mode"] = "exchange"; st["choices"] = opts; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("menu_choose", label="exchange", menu=_render_choices(opts))], stage=st["stage"])
        return pack_response(cid, ["❌ That exchange is not recognized. Please provide a valid exchange (e.g., NSE/BSE or full name)."], stage=st["stage"])

    # ===================== 4) Listed Company flow =====================
    if st["stage"] == STATE["CONFIRM_COMPANY"]:
        if low in YES and st.get("pending_company"):
            st["details"]["company_name"] = st.pop("pending_company")
            st["stage"] = STATE["ASK_HOLDING"]; session_mgr.update_session(cid, st)
            return pack_response(cid, ["Is your holding **Physical** or **Demat**?"], stage=st["stage"])
        if low in NO:
            st.pop("pending_company", None); st["stage"] = STATE["ASK_COMPANY"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("ask_entity", label="company")], stage=st["stage"])
        return pack_response(cid, [tone.say("yes_no_prompt")], stage=st["stage"])

    if st["stage"] == STATE["ASK_COMPANY"]:
        q = (user_msg or "").strip()
        resp = _choice_pick(cid, st, q, mode="company", confirm_stage=STATE["CONFIRM_COMPANY"], reask_prompt=tone.say("ask_entity", label="company"))
        if resp: return resp
        ok, canon = reg.validate_company(q)
        if ok:
            st["pending_company"] = canon; st["stage"] = STATE["CONFIRM_COMPANY"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [f"🔎 Found **{canon}**. {tone.say('yes_no_prompt')}"], stage=st["stage"])
        opts = reg.company_candidates(q) or []
        if opts:
            st["choice_mode"] = "company"; st["choices"] = opts; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("menu_choose", label="company", menu=_render_choices(opts))], stage=st["stage"])
        return pack_response(cid, ["❌ That company is not in the registered list. Please provide a **registered company** name."], stage=st["stage"])

    if st["stage"] == STATE["ASK_HOLDING"]:
        if low in {"physical","p"}:
            st["details"]["holding_mode"] = "Physical"; st["stage"] = STATE["ASK_FOLIO"]; session_mgr.update_session(cid, st)
            return pack_response(cid, ["🧾 Please enter your **Folio Number** (5–25 chars)."], stage=st["stage"])
        if low in {"demat","d"}:
            st["details"]["holding_mode"] = "Demat"; st["stage"] = STATE["ASK_DEMAT"]; session_mgr.update_session(cid, st)
            return pack_response(cid, ["🧾 Please enter your **Demat Account / DP–Client ID** (7–24 chars)."], stage=st["stage"])
        return pack_response(cid, ["Please type **Physical** or **Demat**."], stage=st["stage"])

    if st["stage"] == STATE["ASK_FOLIO"]:
        q = (user_msg or "").strip()
        if not q or not FOLIO_RE.match(q):
            return pack_response(cid, ["That doesn’t look like a valid **Folio Number**. Re-enter."], stage=st["stage"])
        st["details"]["folio_number"] = q; st["stage"] = STATE["WAIT_FILE"]; session_mgr.update_session(cid, st)
        return pack_response(cid, [tone.say("detail_ack") + "\n" + tone.say("file_or_skip")], stage=st["stage"])

    if st["stage"] == STATE["ASK_DEMAT"]:
        q = (user_msg or "").strip()
        if not q or not DEMAT_ACCT_RE.match(q):
            return pack_response(cid, ["That doesn’t look like a valid **Demat Account / DP–Client ID**. Re-enter."], stage=st["stage"])
        st["details"]["demat_account_number"] = q; st["stage"] = STATE["WAIT_FILE"]; session_mgr.update_session(cid, st)
        return pack_response(cid, [tone.say("detail_ack") + "\n" + tone.say("file_or_skip")], stage=st["stage"])

    # ===================== 5) Mutual Fund flow =====================
    if st["stage"] == STATE["CONFIRM_MF"]:
        if low in YES and st.get("pending_mutualfund"):
            st["details"]["mutual_fund_name"] = st.pop("pending_mutualfund")
            st["stage"] = STATE["WAIT_FILE"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [f"✅ Mutual Fund confirmed: **{st['details']['mutual_fund_name']}**\n\n{tone.say('file_or_skip')}"], stage=st["stage"])
        if low in NO:
            st.pop("pending_mutualfund", None); st["stage"] = STATE["ASK_MF"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("ask_entity", label="mutualfund")], stage=st["stage"])
        return pack_response(cid, [tone.say("yes_no_prompt")], stage=st["stage"])

    if st["stage"] == STATE["ASK_MF"]:
        q = (user_msg or "").strip()
        resp = _choice_pick(cid, st, q, mode="mutualfund", confirm_stage=STATE["CONFIRM_MF"], reask_prompt=tone.say("ask_entity", label="mutualfund"))
        if resp: return resp
        ok, canon = reg.validate_mutualfund(q)
        if ok:
            st["pending_mutualfund"] = canon; st["stage"] = STATE["CONFIRM_MF"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [f"🔎 Found **{canon}**. {tone.say('yes_no_prompt')}"], stage=st["stage"])
        opts = reg.mutualfund_candidates(q) or []
        if opts:
            st["choice_mode"] = "mutualfund"; st["choices"] = opts; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("menu_choose", label="mutualfund", menu=_render_choices(opts))], stage=st["stage"])
        return pack_response(cid, ["❌ That Mutual Fund is not in the list. Please provide a **registered Mutual Fund** name."], stage=st["stage"])

    # ===================== 6) Investment Adviser flow =====================
    if st["stage"] == STATE["CONFIRM_ADVISER"]:
        if low in YES and st.get("pending_advisor"):
            st["details"]["investment_advisor_name"] = st.pop("pending_advisor")
            st["stage"] = STATE["WAIT_FILE"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [f"✅ Investment Adviser confirmed: **{st['details']['investment_advisor_name']}**\n\n{tone.say('file_or_skip')}"], stage=st["stage"])
        if low in NO:
            st.pop("pending_advisor", None); st["stage"] = STATE["ASK_ADVISER"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("ask_entity", label="advisor")], stage=st["stage"])
        return pack_response(cid, [tone.say("yes_no_prompt")], stage=st["stage"])

    if st["stage"] == STATE["ASK_ADVISER"]:
        q = (user_msg or "").strip()
        resp = _choice_pick(cid, st, q, mode="advisor", confirm_stage=STATE["CONFIRM_ADVISER"], reask_prompt=tone.say("ask_entity", label="advisor"))
        if resp: return resp
        ok, canon = reg.validate_advisor(q)
        if ok:
            st["pending_advisor"] = canon; st["stage"] = STATE["CONFIRM_ADVISER"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [f"🔎 Found **{canon}**. {tone.say('yes_no_prompt')}"], stage=st["stage"])
        opts = reg.advisor_candidates(q) or []
        if opts:
            st["choice_mode"] = "advisor"; st["choices"] = opts; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("menu_choose", label="advisor", menu=_render_choices(opts))], stage=st["stage"])
        return pack_response(cid, ["❌ That Investment Adviser is not in the list. Please provide a **registered Investment Adviser** name."], stage=st["stage"])

    # ===================== 7) Waiting for optional file =====================
    if st["stage"] == STATE["WAIT_FILE"]:
        if low in {"no", "skip"}:
            st["stage"] = STATE["COLLECT"]; st["details_step_index"] = 0; session_mgr.update_session(cid, st)
            return pack_response(cid, [ask_current_detail(st)], stage=st["stage"])
        # If the UI posts a file separately, the files endpoint will set attachment;
        # here we just gently remind.
        return pack_response(cid, [tone.say("upload_hint")], stage=st["stage"])

    # ===================== 8) Collect details & OTP =====================
    if st["stage"] == STATE["COLLECT"]:
        err = _handle_detail_input(st, user_msg)
        if err: session_mgr.update_session(cid, st); return pack_response(cid, [err], stage=st["stage"])

        if st["stage"] == STATE["VERIFY_OTP"]:
            target = st["otp"].get("target"); code = st["otp"][target].get("code") if target else None
            dev = f" (DEV: {code})" if st.get("OTP_DEBUG_SHOW_CODE", True) and code else ""
            session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("otp_sent", target=target, dev=dev)], stage=st["stage"])

        if st["details_step_index"] < len(DETAIL_STEPS):
            session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("detail_ack") + "\n" + ask_current_detail(st)], stage=st["stage"])

        st["stage"] = STATE["REVIEW"]; session_mgr.update_session(cid, st)
        return pack_response(cid, [_build_review_text(st)], stage=st["stage"])

    if st["stage"] == STATE["VERIFY_OTP"]:
        target = st["otp"].get("target")
        if not target:
            st["stage"] = STATE["COLLECT"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [ask_current_detail(st)], stage=st["stage"])

        if low == "resend":
            _begin_otp(st, target); code = st["otp"][target].get("code")
            dev = f" (DEV: {code})" if st.get("OTP_DEBUG_SHOW_CODE", True) and code else ""
            session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("otp_new", target=target, dev=dev)], stage=st["stage"])

        code_in = re.sub(r"\D", "", user_msg or "")
        if len(code_in) != 6:
            code = st["otp"][target].get("code"); dev = f" (DEV: {code})" if st.get("OTP_DEBUG_SHOW_CODE", True) and code else ""
            return pack_response(cid, [tone.say("otp_bad", target=target, dev=dev)], stage=st["stage"])

        err = _check_otp(st, target, code_in)
        if err:
            code = st["otp"][target].get("code"); dev = f" (DEV: {code})" if st.get("OTP_DEBUG_SHOW_CODE", True) and code else ""
            return pack_response(cid, [f"⚠️ {err}{dev}"], stage=st["stage"])

        st["otp"]["target"] = None
        if st["details_step_index"] < len(DETAIL_STEPS):
            current_key = DETAIL_STEPS[st["details_step_index"]][0]
            if current_key in ("phone", "email"): st["details_step_index"] += 1

        if st["details_step_index"] < len(DETAIL_STEPS):
            st["stage"] = STATE["COLLECT"]; session_mgr.update_session(cid, st)
            return pack_response(cid, ["✅ OTP verified.\n" + ask_current_detail(st)], stage=st["stage"])

        st["stage"] = STATE["REVIEW"]; session_mgr.update_session(cid, st)
        return pack_response(cid, [_build_review_text(st)], stage=st["stage"])

    # ===================== 9) Review & submit =====================
    if st["stage"] == STATE["REVIEW"]:
        if low in YES:
            if not st["otp"]["phone"]["verified"]:
                _begin_otp(st, "phone"); session_mgr.update_session(cid, st)
                code = st["otp"]["phone"].get("code"); dev = f" (DEV: {code})" if st.get("OTP_DEBUG_SHOW_CODE", True) and code else ""
                return pack_response(cid, [tone.say("otp_sent", target="phone", dev=dev)], stage=STATE["VERIFY_OTP"])
            if not st["otp"]["email"]["verified"]:
                _begin_otp(st, "email"); session_mgr.update_session(cid, st)
                code = st["otp"]["email"].get("code"); dev = f" (DEV: {code})" if st.get("OTP_DEBUG_SHOW_CODE", True) and code else ""
                return pack_response(cid, [tone.say("otp_sent", target="email", dev=dev)], stage=STATE["VERIFY_OTP"])

            ref = "CMP-" + datetime.utcnow().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8].upper()
            st["last_ref"] = ref; st["stage"] = STATE["COMPLETED"]; session_mgr.update_session(cid, st)
            return pack_response(cid, [tone.say("submitted", ref=ref)], stage=st["stage"])

        if low in NO:
            st["stage"] = STATE["WAIT_FILE"]; session_mgr.update_session(cid, st)
            return pack_response(cid, ["No problem. You can upload a different file now, or type 'no' to continue without it."], stage=st["stage"])
        return pack_response(cid, [tone.say("yes_no_prompt")], stage=st["stage"])

    # ---------- Fallback ----------
    return pack_response(cid, ["Please describe your complaint (you can attach a file too)."], stage=st["stage"])
