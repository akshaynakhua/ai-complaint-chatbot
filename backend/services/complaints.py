import os, sqlite3, time, subprocess, sys
from datetime import datetime
from . import DB_PATH, DATASET_CSV, TRAIN_SCRIPT, log

def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
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
        broker_name TEXT,
        exchange_name TEXT,
        client_or_dp TEXT,
        company_name TEXT,
        holding_mode TEXT,
        folio_number TEXT,
        demat_account_number TEXT,
        mutual_fund_name TEXT,
        investment_advisor_name TEXT,
        timestamp TEXT
    )
    """)
    conn.commit(); conn.close()
    log.info("DB ready at %s", DB_PATH)

ensure_db()

def lodge_complaint(description, category, sub_category, attachment_path, details) -> str:
    cmp_no = "CMP-" + datetime.utcnow().strftime("%Y%m%d") + "-" + os.urandom(4).hex().upper()
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO complaints (
                complaint_number, description, category, sub_category, attachment_path,
                full_name, phone, email, pan, address, dob,
                broker_name, exchange_name, client_or_dp,
                company_name, holding_mode, folio_number, demat_account_number,
                mutual_fund_name, investment_advisor_name, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cmp_no, description or "", category or "", sub_category or "", attachment_path or "",
            (details or {}).get("full_name",""), (details or {}).get("phone",""), (details or {}).get("email",""),
            (details or {}).get("pan",""), (details or {}).get("address",""), (details or {}).get("dob",""),
            (details or {}).get("broker_name",""), (details or {}).get("exchange_name",""), (details or {}).get("client_or_dp",""),
            (details or {}).get("company_name",""), (details or {}).get("holding_mode",""), (details or {}).get("folio_number",""),
            (details or {}).get("demat_account_number",""), (details or {}).get("mutual_fund_name",""),
            (details or {}).get("investment_advisor_name",""), ts
        ))
        conn.commit(); conn.close()
        log.info("Complaint saved: %s", cmp_no)
    except Exception as e:
        log.exception("Failed to save complaint %s: %s", cmp_no, e)
    return cmp_no

def append_to_csv(complaint_text: str, category: str, sub_category: str):
    header_needed = not os.path.exists(DATASET_CSV)
    for _ in range(3):
        try:
            os.makedirs(os.path.dirname(DATASET_CSV), exist_ok=True)
            with open(DATASET_CSV, "a", encoding="utf-8") as f:
                if header_needed:
                    f.write("complaint_text,category,sub_category\n"); header_needed = False
                safe = (complaint_text or "").replace('"', '""')
                f.write(f"\"{safe}\",{category},{sub_category}\n")
            return
        except PermissionError:
            time.sleep(0.5)

def retrain_async():
    def _run():
        try:
            log.info("Retraining models…")
            subprocess.run([sys.executable, TRAIN_SCRIPT], check=True)
            from .predictor import load_models
            load_models()
            log.info("Retrain complete.")
        except Exception as e:
            log.exception("Retrain failed: %s", e)
    import threading; threading.Thread(target=_run, daemon=True).start()
