from typing import Dict, List, Iterable, Tuple, Optional
import os, csv, glob, io, re, unicodedata

DEBUG = True  # turn off after verifying

try:
    from rapidfuzz import fuzz
    _USE_RF = True
except Exception:
    import difflib
    _USE_RF = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Discovered CSV path
IA_CSV: Optional[str] = None

# Public-ish structures (used by app.py helpers)
_ADVISER_LIST: List[str] = []      # display names
_ADVISOR_LIST: List[str] = _ADVISER_LIST  # alias so either name works
_IA_NORM: Dict[str, str] = {}      # norm->display
_IA_TOKENS: List[Tuple[str, set]] = []
_ALIAS_TO_FULL: Dict[str, str] = {}

# Tunables
HARD_CUTOFF = 60.0
HIGH_CONF_ACCEPT = 90.0

STOP = {
    "investment","investments","investor","adviser","advisor","ia",
    "ltd","limited","pvt","private","llp","plc","india","of","and","&","company","co","services","consultants","advisory"
}

def _discover_csv() -> str:
    """Find a CSV like investment_advisers.csv / investment_advisors.csv in Lists/ or List/."""
    lists_dir_1 = os.path.join(BASE_DIR, "Lists")
    lists_dir_2 = os.path.join(BASE_DIR, "List")

    # Helpful debug prints so you can see what exists
    for d in (lists_dir_1, lists_dir_2):
        try:
            entries = os.listdir(d)
            print(f"[investment_advisers] dir {d} exists? {os.path.isdir(d)} | entries: {entries}")
        except Exception as e:
            print(f"[investment_advisers] cannot list {d}: {e}")

    # common filenames (also tolerate accidental ".csv.csv")
    direct = [
        os.path.join(lists_dir_1, "investment_advisers.csv"),
        os.path.join(lists_dir_1, "investment_advisers.csv.csv"),
        os.path.join(lists_dir_1, "investment_advisors.csv"),
        os.path.join(lists_dir_1, "investment_advisors.csv.csv"),
        os.path.join(lists_dir_2, "investment_advisers.csv"),
        os.path.join(lists_dir_2, "investment_advisers.csv.csv"),
        os.path.join(lists_dir_2, "investment_advisors.csv"),
        os.path.join(lists_dir_2, "investment_advisors.csv.csv"),
    ]
    for p in direct:
        if os.path.exists(p):
            print("[investment_advisers] using:", p)
            return p

    # glob fallback
    patterns = [
        os.path.join(lists_dir_1, "*advis*/*.csv*"),
        os.path.join(lists_dir_1, "*advis*.csv*"),
        os.path.join(lists_dir_2, "*advis*/*.csv*"),
        os.path.join(lists_dir_2, "*advis*.csv*"),
        os.path.join(BASE_DIR, "**", "*advis*.csv*"),
    ]
    for pat in patterns:
        for p in glob.glob(pat, recursive=True):
            if os.path.exists(p):
                print("[investment_advisers] using via glob:", p)
                return p

    # default (may not exist)
    p = os.path.join(lists_dir_1, "investment_advisers.csv")
    print("[investment_advisers] defaulting to:", p, "(exists?", os.path.exists(p), ")")
    return p

def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[^\w& ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()

def _tokenize(s: str, stop: Iterable[str]) -> set:
    return {w for w in _norm_text(s).split() if w and w not in stop}

def _rf_score(a: str, b: str) -> float:
    if _USE_RF:
        # token_set handles word order; partial_ratio is good for substrings/typos
        return max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b))
    else:
        import difflib
        return difflib.SequenceMatcher(None, _norm_text(a), _norm_text(b)).ratio() * 100.0

def load_registry() -> int:
    """Load the advisers CSV. Accepts optional headers:
    - adviser_name / advisor_name / name
    - shortnames / aliases / alias / aka
    """
    global IA_CSV, _ADVISER_LIST, _IA_NORM, _IA_TOKENS, _ALIAS_TO_FULL
    IA_CSV = _discover_csv()
    _ADVISER_LIST, _IA_NORM, _IA_TOKENS, _ALIAS_TO_FULL = [], {}, [], {}

    if not os.path.exists(IA_CSV):
        if DEBUG: print("[investment_advisers] file not found:", IA_CSV)
        return 0

    with open(IA_CSV, "rb") as fb:
        raw = fb.read()
    text = raw.decode("utf-8-sig", errors="replace")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = ","
    if DEBUG: print("[investment_advisers] delimiter:", repr(delim))

    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if r and any(c.strip() for c in r)]
    if not rows:
        return 0
    if DEBUG:
        print("[investment_advisers] total rows read (incl header if present):", len(rows))
        print("[investment_advisers] sample rows:", rows[:3])

    header = [(h or "").strip().lower() for h in rows[0]]
    name_idx = None
    alias_idx = None
    if any(h in {"adviser_name","advisor_name","name"} for h in header):
        for i, h in enumerate(header):
            if h in {"adviser_name","advisor_name","name"}:
                name_idx = i
            if h in {"shortnames","aliases","alias","aka"}:
                alias_idx = i
        data_rows = rows[1:]
        if DEBUG: print("[investment_advisers] header detected:", header)
    else:
        name_idx = 0
        data_rows = rows
        if DEBUG: print("[investment_advisers] no header detected; using col 0 as name")

    for r in data_rows:
        if name_idx is None or name_idx >= len(r):
            continue
        disp = (r[name_idx] or "").strip()
        if not disp:
            continue
        norm = _norm_text(disp)
        if norm not in _IA_NORM:
            _IA_NORM[norm] = disp
            _ADVISER_LIST.append(disp)

        if alias_idx is not None and alias_idx < len(r):
            aliases_raw = (r[alias_idx] or "").strip()
            if aliases_raw:
                for a in re.split(r"[,|/;]\s*", aliases_raw):
                    if not a:
                        continue
                    _ALIAS_TO_FULL[_norm_text(a)] = disp

    _IA_TOKENS = [(name, _tokenize(name, STOP)) for name in _ADVISER_LIST]
    if DEBUG: print("[investment_advisers] loaded count:", len(_ADVISER_LIST))
    return len(_ADVISER_LIST)

def is_registered(name: str) -> bool:
    n = _norm_text(name)
    return (n in _IA_NORM) or (n in _ALIAS_TO_FULL)

def resolve_full_name(name: str) -> Optional[str]:
    n = _norm_text(name)
    if n in _IA_NORM: return _IA_NORM[n]
    if n in _ALIAS_TO_FULL: return _ALIAS_TO_FULL[n]
    return None

def suggest(query: str) -> List[Tuple[str, float]]:
    qn = (query or "").strip()
    if not qn:
        return []
    full = resolve_full_name(qn)
    if full:
        return [(full, 100.0)]

    q_norm = _norm_text(qn)
    q_tokens = _tokenize(qn, STOP)
    short = len(q_norm) <= 4

    res: List[Tuple[str, float]] = []
    for disp, toks in _IA_TOKENS:
        s = _rf_score(qn, disp)
        ld = disp.lower()
        if ld.startswith(q_norm): s += 24
        elif q_norm in ld: s += 12
        if q_tokens & toks: s += 10
        cutoff = 30.0 if short else HARD_CUTOFF
        if s >= cutoff:
            res.append((disp, s))
    res.sort(key=lambda x: x[1], reverse=True)

    if not res and short:
        bucket = []
        for disp, toks in _IA_TOKENS:
            ld = disp.lower()
            if ld.startswith(q_norm) or (q_tokens & toks) or (q_norm in ld):
                bucket.append((disp, 95.0 if ld.startswith(q_norm) else 72.0))
        bucket.sort(key=lambda x: x[1], reverse=True)
        res = bucket[:5]

    return res[:7]

def autodetect_in_text(text: str) -> List[str]:
    t = _norm_text(text or "")
    hits: List[str] = []
    for norm, disp in _IA_NORM.items():
        if norm and norm in t and disp not in hits:
            hits.append(disp)
    for alias_norm, disp in _ALIAS_TO_FULL.items():
        if alias_norm and alias_norm in t and disp not in hits:
            hits.append(disp)
    return hits[:5]
