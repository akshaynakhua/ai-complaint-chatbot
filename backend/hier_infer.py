# backend/hier_infer.py
import os, json, joblib, numpy as np

BASE = os.path.dirname(__file__)
MODELS = os.path.join(BASE, "models")
SUBDIR = os.path.join(MODELS, "subcat_by_category")
META = os.path.join(MODELS, "meta.json")

# load global category model
CAT_MODEL = joblib.load(os.path.join(MODELS, "category_model.joblib"))

# load meta
with open(META, "r", encoding="utf-8") as f:
    META_INFO = json.load(f)

# on-demand cache for subcat models
_SUB_MODELS = {}

def _load_sub_model(cat: str):
    info = META_INFO["categories"].get(cat)
    if not info:
        return None, []
    if not info.get("has_model"):
        return None, info.get("subcats") or []
    if cat not in _SUB_MODELS:
        path = os.path.join(SUBDIR, info["file"])
        _SUB_MODELS[cat] = joblib.load(path)
    return _SUB_MODELS[cat], info.get("subcats") or []

def _topk_from_model(model, text: str, k=3):
    clf = model.named_steps.get("clf")
    labels = clf.classes_
    if hasattr(clf, "decision_function"):
        s = model.decision_function([text])
        if s.ndim == 1:  # binary fix
            s = np.vstack([-s, s]).T
        scores = s[0]
    elif hasattr(clf, "predict_proba"):
        scores = model.predict_proba([text])[0]
    else:
        # fallback: predicted=1.0, others=0.0
        pred = model.predict([text])[0]
        scores = np.array([1.0 if c == pred else 0.0 for c in labels])

    order = np.argsort(-scores)
    top_idx = order[:k]
    return [(labels[i], float(scores[i])) for i in top_idx]

def predict(text: str):
    # 1) predict category
    cat = CAT_MODEL.predict([text])[0]

    # 2) predict sub-category using that category's model
    model, subcats_list = _load_sub_model(cat)
    if model is None:
        # no per-category model → constant fallback to most frequent subcat
        sub = subcats_list[0] if subcats_list else "Other_Subcat"
        return {"category": cat, "sub_category": sub,
                "top_subcats": [(sub, 1.0)], "note": "no per-category model"}

    # main prediction
    sub = model.predict([text])[0]
    top3 = _topk_from_model(model, text, k=3)

    # simple confidence (normalize margins to 0..1)
    scores = np.array([s for _, s in top3])
    conf = float((scores[0] - scores.min()) / (scores.max() - scores.min() + 1e-9))

    return {"category": cat, "sub_category": sub, "top_subcats": top3,
            "confidence": conf}
