# init_db.py
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db", "chatbot.sqlite3")   # use single DB file

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("""
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
)
""")

conn.commit()
conn.close()
print("✅ Database initialized at", DB_PATH)
