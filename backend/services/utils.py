import os, re, uuid
from . import UPLOAD_DIR

ALLOWED_FILE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx"}

def clean_text(t: str) -> str:
    return re.sub(r"[ \t]+", " ", (t or "")).strip()

def format_block(text: str, max_chars: int = 4000) -> str:
    t = (text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if not t: return ""
    lines = []
    for raw in t.split("\n"):
        s = raw.strip()
        if not s:
            lines.append(""); continue
        if re.match(r"^[-•*]\s+", s):
            lines.append(s)
        else:
            while len(s) > 120:
                cut = s.rfind(" ", 0, 120)
                if cut == -1: cut = 120
                lines.append(s[:cut]); s = s[cut:].lstrip()
            lines.append(s)
    out = "\n".join(lines).strip()
    return out[:max_chars] + ("…" if len(out) > max_chars else "")

def _is_allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename or "")[1].lower()
    return ext in ALLOWED_FILE_EXTS

def save_upload(fs) -> str | None:
    if not fs or not fs.filename or not _is_allowed_file(fs.filename):
        return None
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", fs.filename)
    fname = f"{uuid.uuid4().hex}_{safe}"
    path = os.path.join(UPLOAD_DIR, fname)
    fs.save(path)
    return path
