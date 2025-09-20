# backend/config.py
import os

# Base dir = backend/
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Folders
DB_DIR      = os.path.join(BASE_DIR, "db")
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads")
MODELS_DIR  = os.path.join(BASE_DIR, "models")
DATA_DIR    = os.path.join(BASE_DIR, "data")

# Single authoritative DB path (with extension)
DB_PATH     = os.path.join(DB_DIR, "chatbot.sqlite3")

# Env / flags
APP_ENV     = os.environ.get("APP_ENV", "dev").lower()   # dev | prod
IS_PROD     = APP_ENV == "prod"

# Security / limits
MAX_UPLOAD_MB       = int(os.environ.get("MAX_UPLOAD_MB", "16"))
ALLOWED_FILE_EXTS   = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx"}

# Ensure folders exist
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Safety guards
assert DB_PATH.endswith(".sqlite3"), f"Unexpected DB filename: {DB_PATH}"
