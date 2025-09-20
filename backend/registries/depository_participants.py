# registries/depository_participants.py
from typing import Dict, List, Tuple, Optional
import os, csv, glob, io, re, unicodedata

DEBUG = True

try:
    from rapidfuzz import fuzz
    _USE_RF = True
except Exception:
    import difflib
    _USE_RF = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DP_CSV: Optional[str] = None

_DP_LIST: List[str] = []              # display names (what you show to users)
_NORM_TO_DISP: Dict[str, str] = {}    # normalized -> display
_ALIAS_TO_FULL: Dict[str, str] = {}   # alias normalized -> display (e.g., without code)
_HARD_CUTOFF = 60.0

_HEADER_WORDS = {"name", "names", "dp name", "depository participant name", "entity", "entity name", "dp", "code", "dp code"}

def _discover_csv() -> str:
    lists_dir_1 = os.path.join(BASE_DIR, "Lists")
    lists_dir_2 = os.path.join(BASE_DIR, "List")
    for d in (lists_dir_1, lists_dir_2):
        try:
            entries = os.listdir(d)
            print(f"[dp] dir {d} exists? {os.path.isdir(d)} | entries: {entries}")
        except Exception as e:
            print(f"[dp] cannot list {d}: {e}")

    direct = [
        os.path.join(lists_dir_1, "depository_participants.csv"),
        os.path.join(lists_dir_1, "depository_participants.csv.csv"),
        os.path.join(lists_dir_1, "dp.csv"),
        os.path.join(lists_dir_2, "depository_participants.csv"),
        os.path.join(lists_dir_2, "depository_participants.csv.csv"),
        os.path.join(lists_dir_2, "dp.csv"),
    ]
    for p in direct:
        if os.path.exists(p):
            print("[dp] using:", p)
            return p

    for pat in (
        os.path.join(lists_dir_1, "*depositor*.csv*"),
        os.path.join(lists_dir_2, "*depositor*.csv*"),
        os.path.join(BASE_DIR, "**", "*depositor*.csv*"),
    ):
        for p in glob.glob(pat, recursive=True):
            if os.path.exists(p):
                print("[dp] using via glob:", p)
                return p

    p = os.path.join(lists_dir_1, "depository_participants.csv")
    print("[dp] defaulting to:", p, "(exists?", os.path.exists(p), ")")
    return p

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[^\w ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()

def _rf(a: str, b: str) -> float:
    if _USE_RF:
        return max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b))
    else:
        import difflib
        return 100.0 * difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()

def load_registry() -> int:
    global DP_CSV, _DP_LIST, _NORM_TO_DISP, _ALIAS_TO_FULL
    DP_CSV = _discover_csv()
    _DP_LIST, _NORM_TO_DISP, _ALIAS_TO_FULL = [], {}, {}

    if not os.path.exists(DP_CSV):
        if DEBUG: print("[dp] file not found:", DP_CSV)
        return 0

    with open(DP_CSV, "rb") as fb:
        raw = fb.read()
    text = raw.decode("utf-8-sig", errors="replace")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = ","
    if DEBUG: print("[dp] delimiter:", repr(delim))

    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if r and any(c.strip() for c in r)]
    if DEBUG and rows[:3]:
        print("[dp] sample rows:", rows[:3])

    if not rows:
        return 0

    # Header check
    header = [(_norm(h) or "") for h in rows[0]]
    header_like = any(h in _HEADER_WORDS for h in header)
    data_rows = rows[1:] if header_like else rows
    if DEBUG:
        print("[dp] header detected?", header_like, "| header:", rows[0] if header_like else None)

    # Column guess: allow [name], [name, code], [code, name], etc.
    for r in data_rows:
        cols = [c.strip() for c in r if str(c).strip()]
        if not cols: continue

        # pick a name-ish cell
        name = None
        code = None

        if len(cols) == 1:
            name = cols[0]
        else:
            # choose the longest cell as name, the other as possible code if it looks like an id
            sorted_cols = sorted(cols, key=len, reverse=True)
            name = sorted_cols[0]
            # try to find code-like
            for c in cols[1:]:
                if re.search(r"\d", c) or re.match(r"[A-Z]{2}\d+", c, re.I):
                    code = c
                    break

        if not name:
            continue

        # Skip header-ish names and garbage
        nm = _norm(name)
        if not nm or len(nm) < 3 or nm in _HEADER_WORDS or name.strip().lower() in {"name", "names"}:
            continue

        disp = name.upper()
        if code and code.strip():
            disp = f"{disp}({code.strip()})"

        ndisp = _norm(disp)
        if ndisp not in _NORM_TO_DISP:
            _NORM_TO_DISP[ndisp] = disp
            _DP_LIST.append(disp)

        # also map plain name without code as alias
        _ALIAS_TO_FULL[_norm(name)] = disp

    if DEBUG: print("[dp] loaded count:", len(_DP_LIST))
    return len(_DP_LIST)

def resolve_full_name(s: str) -> Optional[str]:
    n = _norm(s)
    if n in _NORM_TO_DISP: return _NORM_TO_DISP[n]
    if n in _ALIAS_TO_FULL: return _ALIAS_TO_FULL[n]
    return None

def suggest(q: str, k: int = 8) -> List[Tuple[str, float]]:
    qn = (q or "").strip()
    if not qn: return []
    full = resolve_full_name(qn)
    if full: return [(full, 100.0)]

    res: List[Tuple[str, float]] = []
    qn_norm = _norm(qn)
    short = len(qn_norm) <= 3

    for disp in _DP_LIST:
        s = _rf(qn, disp)
        ld = disp.lower()
        if ld.startswith(qn_norm): s += 24
        elif qn_norm in ld: s += 12
        cutoff = 30.0 if short else _HARD_CUTOFF
        if s >= cutoff:
            res.append((disp, s))
    res.sort(key=lambda x: x[1], reverse=True)
    return res[:k]
