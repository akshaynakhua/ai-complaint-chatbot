# services/tone.py
from __future__ import annotations
import random

def _pick(options):
    return random.choice(options)

def _nice_label(label: str) -> str:
    mapping = {
        "broker": "Stock Broker",
        "exchange": "Stock Exchange",
        "company": "Listed Company",
        "mutualfund": "Mutual Fund",
        "advisor": "Investment Adviser",
    }
    return mapping.get(label, label.title())

def say(key: str, **kw) -> str:
    name = kw.get("name") or ""
    first = name.split()[0] if name else ""
    you = f"{first}, " if first else ""

    if key == "greet":
        return _pick([
            f"Hey! 👋 {you}Tell me what happened, or attach a PDF/image/DOCX.",
            f"Hello! 👋 {you}Share your complaint in a line or two, or upload a PDF/image/DOCX.",
            f"Hi! 👋 {you}What’s the issue? You can also drop a PDF/image/DOCX."
        ])

    if key == "nudge_desc":
        return _pick([
            f"Thanks! To route this correctly, please add a bit more detail — what happened + the entity name (e.g., broker / mutual fund / company) and the issue (e.g., wrong NAV, dividend not received, order not executed).",
            f"Got it. A little more detail helps me file it right — who is involved (broker/MF/company/adviser) and what exactly went wrong?",
        ])

    if key == "ask_more_detail":
        return _pick([
            "Okay, please re-describe your complaint with a bit more detail.",
            "Could you add a little more detail about the issue and the entity involved?",
        ])

    if key == "confirm_guess":
        cat = kw.get("cat") or "None"
        sub = kw.get("sub") or "None"
        return (
            "🔎 Classification looks like:\n"
            f"📁 Category → {cat}\n"
            f"📂 Sub-category → {sub}\n"
            "Is this correct? (yes / no)"
        )

    if key == "confirm_detected":
        label = _nice_label(kw.get("label", ""))
        value = kw.get("value", "")
        return f"🔎 I detected **{value}** for **{label}**. Is that right? (yes / no)"

    if key == "ask_entity":
        label = _nice_label(kw.get("label", ""))
        return f"🏷️ Please tell me the **{label}** name (as registered)."

    if key == "menu_choose":
        label = _nice_label(kw.get("label", ""))
        menu = kw.get("menu", "")
        return f"❓ Did you mean one of these {label}s? Choose by number:\n\n{menu}"

    if key == "file_or_skip":
        return "If you have any supporting file/screenshot, upload it now. Otherwise type **no** to continue."

    if key == "upload_hint":
        return "You can upload a supporting file now, or type **no** to continue without it."

    if key == "detail_ack":
        return _pick(["✅ Noted.", "👍 Got it.", "✅ Saved."])

    if key == "yes_no_prompt":
        return _pick(["Is this correct? (yes / no)", "Please reply **yes** or **no**."])

    # OTP
    if key == "otp_sent":
        target = kw.get("target", "phone")
        dev = kw.get("dev", "")
        return f"An OTP has been sent to your {target}. Please enter the 6-digit code.{dev}"

    if key == "otp_new":
        target = kw.get("target", "phone")
        dev = kw.get("dev", "")
        return f"New OTP sent to your {target}. Enter the 6-digit code.{dev}"

    if key == "otp_bad":
        target = kw.get("target", "phone")
        dev = kw.get("dev", "")
        return f"Please enter the 6-digit OTP code for your {target}.{dev}"

    if key == "submitted":
        ref = kw.get("ref", "CMP-XXXX")
        return (
            "✅ Complaint submitted successfully!\n"
            f"🔢 Complaint Number → {ref}\n\n"
            "Need to raise another complaint? Type **start**. Say **done** to end."
        )

    if key == "review_intro":
        return _pick([
            "📋 Quick recap of your complaint:",
            "📋 Please review your complaint:"
        ])

    # Conversational / empathetic
    if key == "self_intro":
        return "I’m your complaint assistant. I help you file investor/service complaints and collect the right details."

    if key == "how_are_you":
        return _pick(["All good here — ready to help!", "Doing great and here for you. How can I assist today?"])

    if key == "can_help":
        return "I can understand your complaint, classify it, confirm the right entity (broker/MF/company/adviser), collect KYC, verify via OTP, and submit it with a reference number."

    if key == "ok_reply":
        return _pick(["👍", "👌", "Noted."])

    if key == "thanks_reply":
        return _pick(["Happy to help! 🙌", "Anytime!"])

    if key == "bye_reply":
        return _pick(["Bye! 👋", "Take care! 👋"])

    if key == "ack_wait":
        return _pick(["No rush — take your time. ⏳", "Sure, I’ll be here when you’re ready."])

    if key == "ack_frustration":
        return _pick([
            "Sorry you’re dealing with that. I’ll help you get this filed.",
            "That sounds frustrating — let’s sort it out together."
        ])

    return key
