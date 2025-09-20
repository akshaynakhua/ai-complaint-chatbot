# backend/db_utils.py
import sqlite3
from datetime import datetime
from config import DB_PATH

DDL_COMPLAINTS = """
CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_number TEXT,
    description TEXT,
    category TEXT,
    sub_category TEXT,
    attachment_path TEXT,
    full_name TEXT,
    phone TEXT,
    email TEXT,
    pan TEXT,
    address TEXT,
    dob TEXT,
    timestamp TEXT
);
"""

def get_conn():
    return sqlite3.connect(DB_PATH)

def ensure_db():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute(DDL_COMPLAINTS)
    conn.commit()
    conn.close()
