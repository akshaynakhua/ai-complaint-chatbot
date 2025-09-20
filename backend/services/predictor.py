# backend/services/predictor.py
import os
import joblib
from . import CATEGORY_MODEL_PATH, SUBCATEGORY_MODEL_PATH, log

_category_clf = None
_subcategory_clf = None

def load_models():
    """Load/Reload both models from disk."""
    global _category_clf, _subcategory_clf

    if os.path.exists(CATEGORY_MODEL_PATH):
        _category_clf = joblib.load(CATEGORY_MODEL_PATH)
        log.info("Category model loaded from %s", CATEGORY_MODEL_PATH)
    else:
        _category_clf = None
        log.warning("Missing category model at %s", CATEGORY_MODEL_PATH)

    if os.path.exists(SUBCATEGORY_MODEL_PATH):
        _subcategory_clf = joblib.load(SUBCATEGORY_MODEL_PATH)
        log.info("Sub-category model loaded from %s", SUBCATEGORY_MODEL_PATH)
    else:
        _subcategory_clf = None
        log.warning("Missing sub-category model at %s", SUBCATEGORY_MODEL_PATH)

# load at import time
load_models()

def predict_both(text: str):
    """
    Return (category, subcategory) or (None, None) if models unavailable.
    Safe: never raises on bad input; logs and returns Nones.
    """
    if not text or _category_clf is None or _subcategory_clf is None:
        return None, None
    try:
        cat = _category_clf.predict([text])[0]
        sub = _subcategory_clf.predict([text])[0]
        return cat, sub
    except Exception as e:
        log.exception("Prediction error: %s", e)
        return None, None
