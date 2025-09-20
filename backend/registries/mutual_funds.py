# backend/registries/mutual_funds.py
from __future__ import annotations
import os, re, csv
from typing import List, Tuple, Dict, Optional

# Use rapidfuzz if available (recommended); fallback to difflib
try:
    from rapidfuzz import process, fuzz
    _USE_RF = True
except Exception:
    import difflib
    _USE_RF = False

# Public list used by the app (canonical MF names)
_FUND_LIST: List[str] = []   # e.g., "Aditya Birla Sun Life Mutual Fund"

# ---------- Normalization ----------
_FORMERLY_RE   = re.compile(r"\(.*?formerly.*?\)", re.I)
_SLASH_TAIL_RE = re.compile(r"/.*")
_NOISE_RE = re.compile(
    r"\b(mutual\s+fund|amc|asset\s+management|company|private|pvt|limited|ltd|idf)\b",
    re.I,
)

def _canonicalize(raw: str) -> str:
    if not raw: return ""
    s = _FORMERLY_RE.sub(" ", raw)
    s = _SLASH_TAIL_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Title-case words except short all-caps abbreviations (e.g., MF, UTI)
    s = " ".join(w if (w.isupper() and len(w) <= 4) else w.title() for w in s.split())
    return s

def _norm(s: str) -> str:
    if not s: return ""
    s = s.lower()
    s = _FORMERLY_RE.sub(" ", s)
    s = _SLASH_TAIL_RE.sub(" ", s)
    s = s.replace("&", " and ")
    s = _NOISE_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# ---------- Aliases ----------
# You keep maintaining aliases in your CSV/Excel; this is just an extra safety net.
_EXTRA_ALIASES: Dict[str, List[str]] = {
    "Aditya Birla Sun Life Mutual Fund": [
        "aditya birla sun life", "aditya birla mf", "absl mf",
        "alliance capital mutual fund", "ing mutual fund"
    ],
    "Motilal Oswal Mutual Fund": [
        "motilal", "motilal oswal", "motilal oswal mf", "moamc", "mo mutual fund"
    ],
    "Jio BlackRock Mutual Fund": ["jio blackrock", "jio mf", "blackrock jio"],
    "ICICI Prudential Mutual Fund": ["icici prudential", "icici pru mf", "icici mf"],
    "SBI Mutual Fund": ["sbi mf", "sbi mutual"],
    "HDFC Mutual Fund (Morgan Stanley Mutual Fund)": [
        "hdfc mutual fund", "hdfc mf", "morgan stanley mutual fund"
    ],
    "Nippon India Mutual Fund( Formerly Reliance Mutual Fund)": [
        "nippon india mf", "reliance mutual fund", "reliance mf", "nippon mf"
    ],
    "Franklin Templeton Mutual Fund": ["franklin", "templeton", "franklin templeton", "ft mf"],
}

# alias key (normalized phrase) -> canonical name
_ALIAS_TO_CANON: Dict[str, str] = {}
_ALL_ALIAS_KEYS: List[str] = []

def _rebuild_alias_index():
    """Build lookup dict used by resolve/suggest/autodetect."""
    global _ALIAS_TO_CANON, _ALL_ALIAS_KEYS
    _ALIAS_TO_CANON = {}
    for canon in _FUND_LIST:
        base = _norm(canon)
        # base and base-without "mutual fund"
        for a in {base, re.sub(r"\bmutual fund\b", "", base).strip()}:
            if a:
                _ALIAS_TO_CANON[a] = canon
        for a in _EXTRA_ALIASES.get(canon, []):
            na = _norm(a)
            if na:
                _ALIAS_TO_CANON[na] = canon
    _ALL_ALIAS_KEYS = list(_ALIAS_TO_CANON.keys())

# ---------- Loading ----------
def _read_lines(path: str) -> List[str]:
    out: List[str] = []
    if not os.path.exists(path): return out
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".csv":
            with open(path, "r", encoding="utf-8") as f:
                rdr = csv.reader(f)
                for row in rdr:
                    if row: out.append(row[0].strip())
        else:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s: out.append(s)
    except Exception:
        pass
    return out

def load_registry() -> int:
    """
    Load canonical names from:
      - env MUTUAL_FUNDS_FILE
      - backend/Lists/mutual_funds.txt
      - backend/Lists/mutual_funds.csv
    """
    global _FUND_LIST
    paths: List[str] = []
    envp = os.environ.get("MUTUAL_FUNDS_FILE")
    if envp: paths.append(envp)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # …/backend
    paths += [
        os.path.join(base, "Lists", "mutual_funds.txt"),
        os.path.join(base, "Lists", "mutual_funds.csv"),
    ]

    names: List[str] = []
    for p in paths:
        names = _read_lines(p)
        if names: break

    if names:
        cleaned, seen = [], set()
        for raw in names:
            c = _canonicalize(raw)
            if c and c not in seen:
                cleaned.append(c); seen.add(c)
        _FUND_LIST = cleaned
    else:
        _FUND_LIST = _FUND_LIST or []

    _rebuild_alias_index()
    return len(_FUND_LIST)

def list_all() -> List[str]:
    return list(_FUND_LIST)

# ---------- Safe matching helpers ----------
_GENERIC_TOKENS = {"fund", "funds", "scheme", "schemes", "mf", "nav", "units"}
_MIN_ALIAS_LEN = 3  # ignore 1–2 char keys (e.g., "li", "am")

def _tokenize(s: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", s.lower())

# ---------- Public API used by your app.py ----------
def resolve_full_name(name: str) -> Optional[str]:
    """Map a user-provided name/alias → canonical (exact/alias, or strict fuzzy)."""
    q = _norm(name)
    if not q: return None
    # exact alias hit
    if q in _ALIAS_TO_CANON:
        return _ALIAS_TO_CANON[q]
    # strict fuzzy on alias keys
    cutoff = 92  # keep this high to avoid false positives
    if _USE_RF and _ALL_ALIAS_KEYS:
        match = process.extractOne(q, _ALL_ALIAS_KEYS, scorer=fuzz.token_set_ratio)
        if match:
            ak, score, _ = match
            if score >= cutoff:
                return _ALIAS_TO_CANON.get(ak)
    else:
        cand = difflib.get_close_matches(q, _ALL_ALIAS_KEYS, n=1, cutoff=0.0)
        if cand:
            from difflib import SequenceMatcher
            s = int(100 * SequenceMatcher(None, q, cand[0]).ratio())
            if s >= cutoff:
                return _ALIAS_TO_CANON.get(cand[0])
    return None

def suggest(q: str, limit: int = 8) -> List[Tuple[str, float]]:
    """
    Return [(canonical_name, score)] for UI menus.
    1) Word-boundary exact alias hits (no substring traps like pub**lic** → lic)
    2) Otherwise strict fuzzy on 2..6 word n-grams with high cutoff
    """
    qn = _norm(q)
    if not qn: return []

    # 1) exact alias hits with word boundaries
    hits: List[Tuple[str, float]] = []
    for ak, canon in _ALIAS_TO_CANON.items():
        if not ak or len(ak) < _MIN_ALIAS_LEN:
            continue
        if ak in _GENERIC_TOKENS:
            continue
        if re.search(rf"\b{re.escape(ak)}\b", qn):
            hits.append((canon, 99.0))
    if hits:
        best: Dict[str, float] = {}
        for c, sc in hits:
            if sc > best.get(c, 0): best[c] = sc
        return sorted(best.items(), key=lambda x: x[1], reverse=True)[:limit]

    # 2) strict fuzzy on n-gram windows (robust to long paragraphs)
    cutoff = 92
    words = _tokenize(qn)
    windows: List[str] = []
    for n in range(2, 7):  # 2..6 grams
        for i in range(0, len(words) - n + 1):
            chunk = words[i:i+n]
            if all(w in _GENERIC_TOKENS for w in chunk):
                continue
            windows.append(" ".join(chunk))
    if not windows and words:
        windows = [" ".join(words)]

    scored: List[Tuple[str, float]] = []
    if _USE_RF and _ALL_ALIAS_KEYS:
        best_per_canon: Dict[str, float] = {}
        for win in windows:
            for ak, sc, _ in process.extract(
                win, _ALL_ALIAS_KEYS, scorer=fuzz.token_set_ratio, limit=max(30, limit)
            ):
                if len(ak) < _MIN_ALIAS_LEN or ak in _GENERIC_TOKENS:
                    continue
                if sc >= cutoff:
                    canon = _ALIAS_TO_CANON[ak]
                    if sc > best_per_canon.get(canon, 0.0):
                        best_per_canon[canon] = float(sc)
        scored = sorted(best_per_canon.items(), key=lambda x: x[1], reverse=True)[:limit]
    else:
        from difflib import SequenceMatcher
