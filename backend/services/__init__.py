# backend/services/__init__.py
# Centralize constants/paths used by services

import os, shutil, logging
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BASE_DIR)  # go up from services/ to backend/

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_DIR = os.path.join(BASE_DIR, "db")
DATA_DIR = os.path.join(BASE_DIR, "data")
MODELS_DIR = os.path.join(BASE_DIR, "models")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

DB_PATH = os.path.join(DB_DIR, "chatbot.sqlite3")
DATASET_CSV = os.path.join(DATA_DIR, "complaints_dataset_sample.csv")
TRAIN_SCRIPT = os.path.join(BASE_DIR, "train_model.py")

CATEGORY_MODEL_PATH = os.path.join(MODELS_DIR, "category_model.joblib")
SUBCATEGORY_MODEL_PATH = os.path.join(MODELS_DIR, "sub_category_model.joblib")

# Tesseract (Windows) autodetect
try:
    from PIL import Image  # noqa
    import pytesseract      # noqa
    TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(TESSERACT_CMD):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    else:
        maybe = shutil.which("tesseract.exe")
        if maybe:
            pytesseract.pytesseract.tesseract_cmd = maybe
except Exception:
    pass

# Poppler path for pdf2image on Windows (edit if needed)
POPPLER_PATH = os.environ.get("POPPLER_PATH", r"C:\poppler-24.08.0\Library\bin")

log = logging.getLogger("complaint-bot")
