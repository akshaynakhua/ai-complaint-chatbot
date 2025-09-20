# backend/services/registries_service.py
from __future__ import annotations

import os
import re
import json
import logging
from typing import List, Tuple, Optional, Dict, Any

import joblib

# ----- Logging -----
log = logging.getLogger("complaint-bot.services")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ====== PATHS / CONFIG ======
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.environ.get("MODELS_DIR", os.path.join(BASE_DIR, "models"))

CATEGORY_MODEL_PATH = os.environ.get(
    "CATEGORY_MODEL_PATH",
    os.path.join(MODELS_DIR, "category_model.joblib"),
)

# global fallback sub-model (optional)
GLOBAL_SUB_MODEL_PATH = os.environ.get(
    "SUBCATEGORY_MODEL_PATH",
    os.path.join(MODELS_DIR, "sub_category_model.joblib"),
)

# ✅ matches your folder layout
SUBMODELS_DIR = os.environ.get(
    "SUBMODELS_DIR",
    os.path.join(MODELS_DIR, "subcat_by_category"),
)

# optional metadata file (if you ever want explicit mapping)
META_JSON_PATH = os.path.join(MODELS_DIR, "meta")

# Confidence thresholds (tune if needed)
CAT_THRESHOLD = float(os.environ.get("CAT_THRESHOLD", "0.45"))
SUB_THRESHOLD = float(os.environ.get("SUB_THRESHOLD", "0.40"))

# ====== Models in memory ======
_category_model: Any = None                       # must implement predict / predict_proba
_global_sub_model: Any = None
# key → model; keys include many normalized variants so lookups are robust
_sub_models: Dict[str, Any] = {}
# explicit mapping (if present in META_JSON_PATH)
_meta_map: Dict[str, str] = {}

# ---------- Normalization helpers ----------
def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-")

def _variants(label: str) -> List[str]:
    """
    Generate a wide set of keys for a category label or filename stem.
    Ensures we match:
      'Stock Broker', 'stock-broker', 'Stock_Broker', 'stockbroker', etc.
    """
    raw = (label or "").strip()
    low = raw.lower()
    v = set()

    # raw forms
    v.add(raw)
    v.add(low)

    # classic splits
    v.add(low.replace("_", "-"))
    v.add(low.replace("_", " "))
    v.add(low.replace("-", " "))
    v.add(low.replace(" ", "-"))
    v.add(low.replace(" ", "_"))

    # canonical slug
    v.add(_slug(raw))

    # super compact (remove non-alnum)
    v.add(re.sub(r"[^a-z0-9]", "", low))

    # collapse multiple spaces/dashes
    compact_dash = re.sub(r"[-\s]+", "-", low)
    v.add(compact_dash)

    # remove punctuation then spaces→dash
    v.add(re.sub(r"[^a-z0-9\s]+", "", low).strip().replace(" ", "-"))

    return list({x.strip("- ").strip() for x in v if x.strip()})

def _index_model_with_variants(stem: str, model: Any):
    for k in _variants(stem):
        _sub_models.setdefault(k, model)

def _load_joblib(path: str) -> Optional[Any]:
    try:
        if path and os.path.exists(path):
            m = joblib.load(path)
            log.info("Loaded model: %s", path)
            return m
        log.warning("Model path not found: %s", path)
    except Exception as e:
        log.exception("Failed loading model %s: %s", path, e)
    return None

def _load_category_model():
    global _category_model
    _category_model = _load_joblib(CATEGORY_MODEL_PATH)

def _load_global_sub_model():
    global _global_sub_model
    _global_sub_model = _load_joblib(GLOBAL_SUB_MODEL_PATH)

def _load_meta_mapping():
    """Optional JSON lines or JSON dict at models/meta with {'sub_model_map': {'Category Name': 'file.joblib'}}"""
    global _meta_map
    _meta_map = {}
    if not os.path.exists(META_JSON_PATH):
        return
    try:
        # support both a JSON file or a directory with a file named mapping.json
        path = META_JSON_PATH
        if os.path.isdir(META_JSON_PATH):
            cand = os.path.join(META_JSON_PATH, "mapping.json")
            if os.path.exists(cand):
                path = cand
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "sub_model_map" in data and isinstance(data["sub_model_map"], dict):
            _meta_map = {str(k): str(v) for k, v in data["sub_model_map"].items()}
            log.info("Loaded explicit submodel mapping for %d categories from %s", len(_meta_map), path)
    except Exception as e:
        log.warning("Could not read meta mapping (%s): %s", META_JSON_PATH, e)

def _load_sub_models_dir():
    _sub_models.clear()
    _load_meta_mapping()

    # 1) index files found on disk
    if os.path.isdir(SUBMODELS_DIR):
        for name in os.listdir(SUBMODELS_DIR):
            if not name.lower().endswith(".joblib"):
                continue
            path = os.path.join(SUBMODELS_DIR, name)
            stem = os.path.splitext(name)[0]  # e.g., 'Stock_Broker'
            model = _load_joblib(path)
            if not model:
                continue
            _index_model_with_variants(stem, model)

    # 2) if explicit map is present, also index using category names directly
    for cat, fname in _meta_map.items():
        path = os.path.join(SUBMODELS_DIR, fname)
        model = _load_joblib(path)
        if not model:
            continue
        _index_model_with_variants(cat, model)

    log.info("Per-category sub-model index keys: %d", len(_sub_models))

def reload_models():
    _load_category_model()
    _load_global_sub_model()
    _load_sub_models_dir()

# load once
reload_models()

# ====== Prediction API ======
def _predict_with_proba(model, text: str) -> Tuple[Optional[str], float]:
    """Return (label, max_prob). If model has no predict_proba, return (label, 1.0)."""
    if not model or not text:
        return None, 0.0
    try:
        if hasattr(model, "predict_proba"):
            proba = model.predict_proba([text])[0]
            idx = int(proba.argmax())
            label = model.classes_[idx]
            return str(label), float(proba[idx])
        # no proba method → best effort
        label = model.predict([text])[0]
        return str(label), 1.0
    except Exception as e:
        log.exception("Model prediction failed: %s", e)
        try:
            label = model.predict([text])[0]
            return str(label), 1.0
        except Exception:
            return None, 0.0

def _lookup_sub_model_for_category(cat: str) -> Optional[Any]:
    """
    Try multiple keys so we work with filenames produced by train_hierarchical.py
    like 'Stock_Broker.joblib' without renaming.
    """
    if not cat:
        return None
    keys = _variants(cat)
    for k in keys:
        if k in _sub_models:
            return _sub_models[k]
    # extra tolerant: also try variants of slug itself
    slug = _slug(cat)
    for k in _variants(slug):
        if k in _sub_models:
            return _sub_models[k]
    return None

def predict_both(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Hierarchical prediction with robust sub-model lookup:
      1) Predict Category
      2) If confident, find matching per-category sub-model by many name variants
      3) Predict Sub-category (or fallback to global sub-model)
    """
    t = (text or "").strip()
    if not t or _category_model is None:
        log.warning("predict_both: text empty or category model missing")
        return None, None

    # Category
    cat, p_cat = _predict_with_proba(_category_model, t)
    if not cat or p_cat < CAT_THRESHOLD:
        log.info("Category below threshold: %s (%.3f)", cat, p_cat)
        return None, None

    # Sub-category via robust per-category lookup
    sub = None
    model = _lookup_sub_model_for_category(cat)
    if model is None:
        model = _global_sub_model  # fallback (optional)
        if model is None:
            return cat, None

    sub, p_sub = _predict_with_proba(model, t)
    if not sub or p_sub < SUB_THRESHOLD:
        log.info("Sub-category below threshold: %s (%.3f) for category %s", sub, p_sub, cat)
        sub = None

    return cat, sub

# ====== Registries glue (brokers / exchanges / companies / mutual funds / advisers) ======
from registries import brokers as RBro
from registries import exchanges as REx
from registries import Listed_Company_Equity_Issue_DividendTransfer_Transmission_Duplicate_Shares_BonusShares_etc as LCEI
from registries import mutual_funds as RMF
from registries import investment_advisers as RIA

# Load registries (idempotent if already loaded elsewhere)
try:
    RBro.load_registry(); REx.load_registry(); LCEI.load_registry(); RMF.load_registry(); RIA.load_registry()
except Exception as e:
    log.exception("Registry load error (safe to ignore if already loaded): %s", e)

def _list_brokers() -> List[str]:
    return list(getattr(RBro, "_BROKER_LIST", []))

def _list_exchanges() -> List[str]:
    return list(getattr(REx, "_EXCH_LIST", []))

def _list_companies() -> List[str]:
    return list(getattr(LCEI, "_COMPANY_LIST", []) or getattr(LCEI, "_LIST", []))

def _list_mutualfunds() -> List[str]:
    return list(getattr(RMF, "_FUND_LIST", []) or getattr(RMF, "_MF_LIST", []))

def _list_advisers() -> List[str]:
    return list(getattr(RIA, "_ADVISER_LIST", []) or getattr(RIA, "_ADVISOR_LIST", []))

def broker_candidates(q: str, k: int = 8) -> List[str]:
    if not (q or "").strip(): return []
    try:
        pairs = RBro.suggest(q) or []
        seen, out = set(), []
        for name, _score in pairs:
            if name not in seen:
                out.append(name); seen.add(name)
            if len(out) >= k: break
        if out: return out
    except Exception:
        pass
    qn = (q or "").lower()
    return [n for n in _list_brokers() if qn in n.lower()][:k]

def exchange_suggestions(q: str, k: int = 6) -> List[str]:
    if not (q or "").strip(): return []
    try:
        pairs = REx.suggest(q) or []
        seen, out = set(), []
        for name, _score in pairs:
            if name not in seen:
                out.append(name); seen.add(name)
            if len(out) >= k: break
        if out: return out
    except Exception:
        pass
    qn = (q or "").lower()
    return [n for n in _list_exchanges() if qn in n.lower()][:k]

def company_candidates(q: str, k: int = 8) -> List[str]:
    if not (q or "").strip(): return []
    try:
        pairs = LCEI.suggest(q) or []
        seen, out = set(), []
        for name in [p[0] if isinstance(p, (list, tuple)) else p for p in pairs]:
            if name not in seen:
                out.append(name); seen.add(name)
            if len(out) >= k: break
        if out: return out
    except Exception:
        pass
    qn = (q or "").lower()
    return [n for n in _list_companies() if qn in n.lower()][:k]

def mutualfund_candidates(q: str, k: int = 8) -> List[str]:
    if not (q or "").strip(): return []
    try:
        pairs = RMF.suggest(q) or []
        seen, out = set(), []
        for name in [p[0] if isinstance(p, (list, tuple)) else p for p in pairs]:
            if name not in seen:
                out.append(name); seen.add(name)
            if len(out) >= k: break
        if out: return out
    except Exception:
        pass
    qn = (q or "").lower()
    return [n for n in _list_mutualfunds() if qn in n.lower()][:k]

def advisor_candidates(q: str, k: int = 8) -> List[str]:
    if not (q or "").strip(): return []
    try:
        pairs = RIA.suggest(q) or []
        seen, out = set(), []
        for name in [p[0] if isinstance(p, (list, tuple)) else p for p in pairs]:
            if name not in seen:
                out.append(name); seen.add(name)
            if len(out) >= k: break
        if out: return out
    except Exception:
        pass
    qn = (q or "").lower()
    return [n for n in _list_advisers() if qn in n.lower()][:k]

def validate_broker(name: str) -> Tuple[bool, Optional[str]]:
    q = (name or "").strip().lower()
    if not q: return False, None
    for b in _list_brokers():
        if b.lower() == q: return True, b
    full = RBro.resolve_full_name(name or "")
    if full: return True, full
    sug = RBro.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if float(score) >= 92.0: return True, cand
    return False, None

def validate_exchange(name: str) -> Tuple[bool, Optional[str]]:
    q = (name or "").strip().lower()
    if not q: return False, None
    for e in _list_exchanges():
        if e.lower() == q: return True, e
    full = REx.resolve_full_name(name or "")
    if full: return True, full
    sug = REx.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if float(score) >= 90.0: return True, cand
    return False, None

def validate_company(name: str) -> Tuple[bool, Optional[str]]:
    q = (name or "").strip().lower()
    if not q: return False, None
    for c in _list_companies():
        if c.lower() == q: return True, c
    full = LCEI.resolve_full_name(name or "")
    if full: return True, full
    sug = LCEI.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if float(score) >= 90.0: return True, cand
    return False, None

def validate_mutualfund(name: str) -> Tuple[bool, Optional[str]]:
    q = (name or "").strip().lower()
    if not q: return False, None
    for m in _list_mutualfunds():
        if m.lower() == q: return True, m
    full = RMF.resolve_full_name(name or "")
    if full: return True, full
    sug = RMF.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if float(score) >= 90.0: return True, cand
    return False, None

def validate_advisor(name: str) -> Tuple[bool, Optional[str]]:
    q = (name or "").strip().lower()
    if not q: return False, None
    for a in _list_advisers():
        if a.lower() == q: return True, a
    full = RIA.resolve_full_name(name or "")
    if full: return True, full
    sug = RIA.suggest(name or "")
    if sug:
        cand, score = sug[0]
        if float(score) >= 90.0: return True, cand
    return False, None

# ====== Simple entity auto-detect from free text ======
try:
    from rapidfuzz import process as rf_process, fuzz as rf_fuzz
    _USE_RF = True
except Exception:
    _USE_RF = False
    import difflib

def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (s or "").upper()).strip()

def _best_one(q: str, choices: List[str]) -> Tuple[int, Optional[str]]:
    if not choices:
        return 0, None
    if _USE_RF:
        best = rf_process.extractOne(q, choices, scorer=rf_fuzz.partial_ratio)
        if not best:
            return 0, None
        name, score, _ = best
        return int(score), name
    else:
        if not q:
            return 0, None
        cand = difflib.get_close_matches(q, choices, n=1, cutoff=0.0)
        if not cand:
            return 0, None
        from difflib import SequenceMatcher
        s = int(100 * SequenceMatcher(None, q, cand[0]).ratio())
        return s, cand[0]

def detect_broker_and_exchange_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
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
                broker = b; break
    if exchange:
        for e in _list_exchanges():
            if e.upper() == exchange:
                exchange = e; break
    return broker, exchange
