# backend/registries/rtas.py
from typing import Dict, List, Iterable, Tuple, Optional
import os, csv, glob, io, re, unicodedata

DEBUG = True  # set False after it works

try:
    from rapidfuzz import fuzz
    _USE_RF = True
except Exception:
    import difflib
    _USE_RF = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RTA_CSV: Optional[str] = None

_RTA_NORM: Dict[str, str] = {}
_RTA_LIST: List[str] = []
_RTA_TOKENS: List[Tuple[str, set]] = []
_ALIAS_TO_FULL: Dict[str, str] = {}

HARD_CUTOFF = 60.0

RTA_STOP = {
    "registrar", "and", "&", "share", "transfer", "agent",
    "pvt", "private", "limited", "ltd", "llp", "llc", "india",
    "services", "service", "company", "co"
}

def _discover_csv() -> str:
    lists_dir_1 = os.path.join(BASE_DIR, "Lists")
    lists_dir_2 = os.path.join(BASE_DIR, "List")
    for d in (lists_dir_1, lists_dir_2):
        try:
            entries = os.listdir(d)
            print(f"[rtas] dir {d} exists? {os.path.isdir(d)} | entries: {entries}")
        except Exception as e:
            print(f"[rtas] cannot list {d}: {e}")
    direct = [
        os.path.join(lists_dir_1, "rtas.csv"),
        os.path.join(lists_dir_1, "rta.csv"),
        os.path.join(lists_dir_1, "rtas.csv.csv"),
        os.path.join(lists_dir_2, "rtas.csv"),
        os.path.join(lists_dir_2, "rta.csv"),
        os.path.join(lists_dir_2, "rtas.csv.csv"),
    ]
    for p in direct:
        if os.path.exists(p):
            print("[rtas] using:", p); return p
    patterns = [
        os.path.join(lists_dir_1, "*rta*.csv*"),
        os.path.join(lists_dir_2, "*rta*.csv*"),
        os.path.join(BASE_DIR, "**", "*rta*.csv*"),
        os.path.join(BASE_DIR, "**", "*registrar*transfer*agent*.csv*"),
    ]
    for pat in patterns:
        for p in glob.glob(pat, recursive=True):
            if os.path.exists(p):
                print("[rtas] using via glob:", p); return p
    p = os.path.join(lists_dir_1, "rtas.csv")
    print("[rtas] defaulting to:", p, "(exists?", os.path.exists(p), ")")
    return p

def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[^\w& ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()

def _tokenize(s: str, stop: Iterable[str]) -> set:
    return {w for w in _norm_text(s).split() if w and w not in stop}

def _rf_score(a: str, b: str) -> float:
    if _USE_RF:
        return max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b))
    else:
        import difflib
        return difflib.SequenceMatcher(None, _norm_text(a), _norm_text(b)).ratio() * 100.0

def load_registry() -> int:
    global RTA_CSV, _RTA_NORM, _RTA_LIST, _RTA_TOKENS, _ALIAS_TO_FULL
    RTA_CSV = _discover_csv()
    _RTA_NORM, _RTA_LIST, _RTA_TOKENS, _ALIAS_TO_FULL = {}, [], [], {}

    if not os.path.exists(RTA_CSV):
        if DEBUG: print("[rtas] file not found:", RTA_CSV)
        return 0

    with open(RTA_CSV, "rb") as fb:
        raw = fb.read()
    text = raw.decode("utf-8-sig", errors="replace")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = ","
    if DEBUG: print("[rtas] delimiter:", repr(delim))

    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if r and any(c.strip() for c in r)]
    if DEBUG:
        print("[rtas] total rows read (incl header if present):", len(rows))
        if rows[:3]: print("[rtas] sample rows:", rows[:3])

    if not rows:
        return 0

    header = [(h or "").strip().lower() for h in rows[0]]
    name_idx = None
    alias_idx = None
    if any(h in {"rta_name","name"} for h in header):
        for i, h in enumerate(header):
            if h in {"rta_name","name"}: name_idx = i
            if h in {"aliases","alias","shortnames","aka"}: alias_idx = i
        data_rows = rows[1:]
        if DEBUG: print("[rtas] header detected:", header)
    else:
        name_idx = 0
        data_rows = rows
        if DEBUG: print("[rtas] no header detected; using col 0 as name")

    for r in data_rows:
        if name_idx is None or name_idx >= len(r):
            continue
        disp = (r[name_idx] or "").strip()
        if not disp:
            continue
        norm = _norm_text(disp)
        if norm not in _RTA_NORM:
            _RTA_NORM[norm] = disp
            _RTA_LIST.append(disp)

        if alias_idx is not None and alias_idx < len(r):
            aliases_raw = (r[alias_idx] or "").strip()
            if aliases_raw:
                for a in re.split(r"[,|/;]\s*", aliases_raw):
                    if not a: continue
                    _ALIAS_TO_FULL[_norm_text(a)] = disp

    _RTA_TOKENS = [(name, _tokenize(name, RTA_STOP)) for name in _RTA_LIST]
    if DEBUG: print("[rtas] loaded count:", len(_RTA_LIST))
    return len(_RTA_LIST)

def is_registered(name: str) -> bool:
    n = _norm_text(name)
    return (n in _RTA_NORM) or (n in _ALIAS_TO_FULL)

def resolve_full_name(name: str) -> Optional[str]:
    n = _norm_text(name)
    if n in _RTA_NORM: return _RTA_NORM[n]
    if n in _ALIAS_TO_FULL: return _ALIAS_TO_FULL[n]
    return None

def suggest(query: str) -> List[Tuple[str,float]]:
    qn = (query or "").strip()
    if not qn: return []
    full = resolve_full_name(qn)
    if full: return [(full, 100.0)]

    q_norm = _norm_text(qn)
    q_tokens = _tokenize(qn, RTA_STOP)
    short = len(q_norm) <= 4

    res: List[Tuple[str,float]] = []
    for disp, toks in _RTA_TOKENS:
        s = _rf_score(qn, disp)
        ld = disp.lower()
        if ld.startswith(q_norm): s += 24
        elif q_norm in ld: s += 12
        if q_tokens & toks: s += 10
        cutoff = 30.0 if short else HARD_CUTOFF
        if s >= cutoff: res.append((disp, s))
    res.sort(key=lambda x: x[1], reverse=True)

    if not res and short:
        bucket = []
        for disp, toks in _RTA_TOKENS:
            ld = disp.lower()
            if ld.startswith(q_norm) or (q_tokens & toks) or (q_norm in ld):
                bucket.append((disp, 95.0 if ld.startswith(q_norm) else 72.0))
        bucket.sort(key=lambda x: x[1], reverse=True)
        res = bucket[:5]
    return res[:7]

def autodetect_in_text(text: str) -> List[str]:
    t = _norm_text(text or "")
    hits: List[str] = []
    for norm, disp in _RTA_NORM.items():
        if norm and norm in t and disp not in hits:
            hits.append(disp)
    for alias_norm, disp in _ALIAS_TO_FULL.items():
        if alias_norm and alias_norm in t and disp not in hits:
            hits.append(disp)
    return hits[:5]
