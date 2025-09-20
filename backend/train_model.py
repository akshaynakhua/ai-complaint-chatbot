import os
import sys
import joblib
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn import metrics

print("🚀 Starting model training (sub-cat boosted, Windows-safe)…")

# -------------------------
# Paths
# -------------------------
BASE_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.join(BASE_DIR, "data", "complaints_dataset_sample.csv")
MODELS_DIR = os.path.join(BASE_DIR, "models")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# -------------------------
# Load data
# -------------------------
df = pd.read_csv(DATA_PATH)
df.columns = df.columns.str.strip()

needed = ["complaint_text", "category", "sub_category"]
missing = [c for c in needed if c not in df.columns]
if missing:
    raise ValueError(f"❌ Missing columns: {missing}. Found: {list(df.columns)}")

# basic cleaning
df = df.dropna(subset=needed).copy()
df["complaint_text"] = df["complaint_text"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
df["category"] = df["category"].astype(str).str.strip()
df["sub_category"] = df["sub_category"].astype(str).str.strip()

print(f"✅ Data loaded. Rows: {len(df)}")
print(f"📊 Columns found: {list(df.columns)}")

# -------------------------
# Handle ultra-rare labels so stratify/cv won't break
# -------------------------
def collapse_rare(series: pd.Series, min_count: int, other_label: str) -> pd.Series:
    vc = series.value_counts()
    rare_labels = vc[vc < min_count].index
    if len(rare_labels) > 0:
        print(f"ℹ️ Collapsing {len(rare_labels)} labels (<{min_count}) into '{other_label}'")
        series = series.where(~series.isin(rare_labels), other_label)
    return series

# Keep category stable but safe: ensure ≥2 samples/class
df["category"] = collapse_rare(df["category"], min_count=2, other_label="Other")
# Sub-category is usually very imbalanced: require ≥5
df["sub_category"] = collapse_rare(df["sub_category"], min_count=5, other_label="Other_Subcat")

# -------------------------
# Features / labels
# -------------------------
X = df["complaint_text"]
y_cat = df["category"]
y_sub = df["sub_category"]

# -------------------------
# Vectorizers
# -------------------------
# CATEGORY (word-only)
cat_tfidf = TfidfVectorizer(
    lowercase=True,
    stop_words="english",
    max_df=0.85,
    min_df=5,
    ngram_range=(1, 2),
)

# SUB-CATEGORY (word + char)
sub_word_tfidf = TfidfVectorizer(
    lowercase=True,
    stop_words=None,      # keep Hinglish/abbrev tokens
    max_df=0.90,
    min_df=5,
    ngram_range=(1, 2),
)
sub_char_tfidf = TfidfVectorizer(
    analyzer="char",
    ngram_range=(3, 4),   # good balance of signal vs memory
    min_df=5,
)

sub_features = FeatureUnion([
    ("word", sub_word_tfidf),
    ("char", sub_char_tfidf),
])

# -------------------------
# Pipelines
# -------------------------
category_model = Pipeline([
    ("tfidf", cat_tfidf),
    ("clf", LogisticRegression(max_iter=1000)),
])

sub_category_model = Pipeline([
    ("feats", sub_features),
    ("clf", LinearSVC(class_weight="balanced", max_iter=5000, C=1.0)),
])

# -------------------------
# Stratified splits
# -------------------------
Xtr_c, Xte_c, ytr_c, yte_c = train_test_split(
    X, y_cat, test_size=0.20, random_state=42, stratify=y_cat
)
Xtr_s, Xte_s, ytr_s, yte_s = train_test_split(
    X, y_sub, test_size=0.20, random_state=42, stratify=y_sub
)

# -------------------------
# Train + Evaluate: CATEGORY
# -------------------------
print("🧠 Training CATEGORY model (LogisticRegression)…")
category_model.fit(Xtr_c, ytr_c)
yp_c = category_model.predict(Xte_c)

cat_acc = metrics.accuracy_score(yte_c, yp_c)
cat_f1_macro = metrics.f1_score(yte_c, yp_c, average="macro", zero_division=0)
print(f"✅ Category Accuracy: {cat_acc:.3f} | Macro-F1: {cat_f1_macro:.3f}")

# Save model immediately (so it exists even if sub-cat fails)
cat_path = os.path.join(MODELS_DIR, "category_model.joblib")
joblib.dump(category_model, cat_path)
print(f"💾 Saved category model → {cat_path}")

# Save metrics tables
cat_report = metrics.classification_report(yte_c, yp_c, output_dict=True, zero_division=0)
cat_df = pd.DataFrame(cat_report).transpose()
cat_csv = os.path.join(REPORTS_DIR, "metrics_category.csv")
cat_df.to_csv(cat_csv, index=True)
print(f"📊 Category metrics CSV → {cat_csv}")

# -------------------------
# Train + Evaluate: SUB-CATEGORY
# -------------------------
print("🧠 Training SUB-CATEGORY model (LinearSVC word+char)…")
sub_category_model.fit(Xtr_s, ytr_s)
yp_s = sub_category_model.predict(Xte_s)

sub_acc = metrics.accuracy_score(yte_s, yp_s)
sub_f1_macro = metrics.f1_score(yte_s, yp_s, average="macro", zero_division=0)
sub_f1_weighted = metrics.f1_score(yte_s, yp_s, average="weighted", zero_division=0)
print(f"✅ Sub-Category Accuracy: {sub_acc:.3f} | Macro-F1: {sub_f1_macro:.3f} | Weighted-F1: {sub_f1_weighted:.3f}")

# Save model
sub_path = os.path.join(MODELS_DIR, "sub_category_model.joblib")
joblib.dump(sub_category_model, sub_path)
print(f"💾 Saved sub-category model → {sub_path}")

# Save metrics tables
sub_report = metrics.classification_report(yte_s, yp_s, output_dict=True, zero_division=0)
sub_df = pd.DataFrame(sub_report).transpose()
sub_csv = os.path.join(REPORTS_DIR, "metrics_sub_category.csv")
sub_df.to_csv(sub_csv, index=True)
print(f"📊 Sub-category metrics CSV → {sub_csv}")

# -------------------------
# Optional: single Excel with both sheets
# -------------------------
xlsx_path = os.path.join(REPORTS_DIR, "metrics.xlsx")
try:
    with pd.ExcelWriter(xlsx_path, engine="xlsxwriter") as writer:
        cat_df.to_excel(writer, sheet_name="Category", index=True)
        sub_df.to_excel(writer, sheet_name="SubCategory", index=True)
    print(f"📘 Excel metrics → {xlsx_path} (sheets: Category, SubCategory)")
except Exception as e:
    print(f"ℹ️ Excel export skipped ({e}). CSVs are already saved.")

print("✅ Training complete.")
