import os, json, joblib, pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn import metrics

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data", "complaints_dataset_sample.csv")
MODELS = os.path.join(BASE, "models")
SUBDIR = os.path.join(MODELS, "subcat_by_category")
META = os.path.join(MODELS, "meta.json")
os.makedirs(MODELS, exist_ok=True)
os.makedirs(SUBDIR, exist_ok=True)

print("🚀 Hierarchical training start…")
df = pd.read_csv(DATA).dropna(subset=["complaint_text", "category", "sub_category"]).copy()
for c in ["complaint_text","category","sub_category"]:
    df[c] = df[c].astype(str).str.strip()
df["complaint_text"] = df["complaint_text"].str.replace(r"\s+", " ", regex=True)

def collapse_rare(series: pd.Series, min_count: int, other_label: str) -> pd.Series:
    vc = series.value_counts()
    rare = vc[vc < min_count].index
    if len(rare) > 0:
        print(f"ℹ️ Collapsing {len(rare)} labels (<{min_count}) into '{other_label}'")
        series = series.where(~series.isin(rare), other_label)
    return series

# Ensure category has ≥2 per class
df["category"] = collapse_rare(df["category"], 2, "Other")

# ========== CATEGORY model ==========
cat_vec = TfidfVectorizer(lowercase=True, stop_words="english",
                          max_df=0.85, min_df=5, ngram_range=(1,2))
cat_model = Pipeline([("tfidf", cat_vec),
                      ("clf", LogisticRegression(max_iter=1000))])

Xc, yc = df["complaint_text"], df["category"]
Xtr_c, Xte_c, ytr_c, yte_c = train_test_split(Xc, yc, test_size=0.2,
                                              random_state=42, stratify=yc)
cat_model.fit(Xtr_c, ytr_c)
cat_acc = metrics.accuracy_score(yte_c, cat_model.predict(Xte_c))
print(f"✅ Category accuracy: {cat_acc:.3f}")
joblib.dump(cat_model, os.path.join(MODELS, "category_model.joblib"))

# ========== PER-CATEGORY sub-category models ==========
meta = {"version": 1, "categories": {}}
cats = sorted(df["category"].unique().tolist())

for cat in cats:
    d = df[df["category"] == cat].copy()

    # First collapse: require ≥5 per subcat
    d["sub_category"] = collapse_rare(d["sub_category"], 5, "Other_Subcat")

    # Second collapse: enforce ≥2 per subcat (needed for stratify)
    d["sub_category"] = collapse_rare(d["sub_category"], 2, "Other_Subcat")

    subs = sorted(d["sub_category"].unique().tolist())
    if len(subs) <= 1:
        print(f"[skip] {cat}: only one sub-category after collapsing → no model trained")
        meta["categories"][cat] = {"has_model": False, "subcats": subs}
        continue

    # dynamic test size based on rarest subcat count
    minc = d["sub_category"].value_counts().min()
    if minc == 2:
        test_size = 0.5
    elif minc == 3:
        test_size = 1/3
    else:
        test_size = 0.2

    try:
        # features
        word = TfidfVectorizer(lowercase=True, stop_words=None,
                               max_df=0.90, min_df=5, ngram_range=(1,2))
        char = TfidfVectorizer(analyzer="char", ngram_range=(3,4), min_df=5)
        feats = FeatureUnion([("word", word), ("char", char)])
        sub_model = Pipeline([("feats", feats),
                              ("clf", LinearSVC(class_weight="balanced",
                                                max_iter=5000, C=1.0))])

        Xs, ys = d["complaint_text"], d["sub_category"]
        Xtr_s, Xte_s, ytr_s, yte_s = train_test_split(
            Xs, ys, test_size=test_size, random_state=42, stratify=ys
        )

        sub_model.fit(Xtr_s, ytr_s)
        s_acc = metrics.accuracy_score(yte_s, sub_model.predict(Xte_s))
        print(f"✅ Sub-category acc [{cat}]: {s_acc:.3f} (classes={len(subs)}, min_count={minc}, test_size={test_size:.2f})")

        fname = cat.replace("/", "_").replace("\\","_").replace(" ", "_")
        joblib.dump(sub_model, os.path.join(SUBDIR, f"{fname}.joblib"))
        meta["categories"][cat] = {"has_model": True, "file": f"{fname}.joblib", "subcats": subs}

    except Exception as e:
        # If split still fails for some corner case, skip but keep meta for fallback
        print(f"[skip] {cat}: training failed due to '{e}'. Using fallback (most frequent subcat).")
        meta["categories"][cat] = {"has_model": False, "subcats": subs}

# Save meta
with open(META, "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)

print("🎉 Done. Saved: category model, per-category subcat models where possible, and meta.json")
