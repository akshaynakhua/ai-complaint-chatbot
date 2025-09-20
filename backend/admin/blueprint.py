from __future__ import annotations

import os
import re
import io
import time
import math
import sqlite3
import mimetypes
import secrets
from functools import wraps
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd
from flask import Blueprint, request, jsonify, send_file, abort

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH    = os.path.join(BASE_DIR, "db", "chatbot.sqlite3")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
MODELS_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR   = os.path.join(BASE_DIR, "data")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")

# Token 
_TOKENS: Dict[str, float] = {}  
TOKEN_TTL_SECS = 60 * 60 * 6    

bp = Blueprint("admin", __name__)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def dict_row(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}

def _issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = time.time() + TOKEN_TTL_SECS
    return token

def _is_valid_token(token: str) -> bool:
    exp = _TOKENS.get(token)
    if not exp:
        return False
    if time.time() > exp:
        try:
            del _TOKENS[token]
        except KeyError:
            pass
        return False
    return True

def require_admin(fn):
    @wraps(fn)
    def _wrap(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        m = re.match(r"^\s*Bearer\s+(.+)$", auth)
        if not m:
            return jsonify({"error": "Unauthorized"}), 401
        token = m.group(1).strip()
        if not _is_valid_token(token):
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return _wrap

IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp"}
PDF_EXTS   = {"pdf"}

def _basename_from_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return os.path.basename(path)

def _is_previewable(basename: str) -> bool:
    ext = (basename.rsplit(".", 1)[-1] if "." in basename else "").lower()
    return (ext in IMAGE_EXTS) or (ext in PDF_EXTS)


@bp.post("/api/login")
def api_login():
    """
    Body: { "username": "...", "password": "..." }
    Returns: { token: "..." }
    """
    data = request.get_json(silent=True) or {}
    u = (data.get("username") or "").strip()
    p = (data.get("password") or "").strip()

    if u == ADMIN_USER and p == ADMIN_PASS:
        token = _issue_token()
        return jsonify({
            "token": token,
            "expires_in": TOKEN_TTL_SECS
        })

    return jsonify({"error": "Invalid credentials"}), 401


@bp.get("/api/complaints")
@require_admin
def list_complaints():
    """
    Query params:
      q: search text (optional)
      page: 1-based page number (default=1)
      size: page size (default=20)
    Returns: { total, page, size, items: [...] }
    """
    q    = (request.args.get("q") or "").strip() or None
    page = max(1, int(request.args.get("page", "1")))
    size = min(100, max(1, int(request.args.get("size", "20"))))
    offset = (page - 1) * size

    conn = get_db()
    params = {}
    where = ""
    if q:
        where = """
        WHERE description LIKE :q
           OR complaint_number LIKE :q
           OR category LIKE :q
           OR sub_category LIKE :q
           OR full_name LIKE :q
           OR phone LIKE :q
           OR email LIKE :q
        """
        params["q"] = f"%{q}%"

    total = conn.execute(f"SELECT COUNT(*) FROM complaints {where}", params).fetchone()[0]

    sql = f"""
      SELECT id, complaint_number, category, sub_category, phone, email, timestamp
      FROM complaints
      {where}
      ORDER BY id DESC
      LIMIT :size OFFSET :offset
    """
    params.update({"size": size, "offset": offset})
    rows = [dict_row(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()

    return jsonify({
        "total": total,
        "page": page,
        "size": size,
        "items": rows,
    })


@bp.get("/api/complaints/<int:cid>")
@require_admin
def complaint_detail(cid: int):
    conn = get_db()
    row = conn.execute("""
        SELECT id, complaint_number, description, category, sub_category,
               phone, email, full_name, pan, address, dob, timestamp, attachment_path
        FROM complaints WHERE id = ?
    """, (cid,)).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Not found"}), 404

    d = dict_row(row)
    basename = _basename_from_path(d.get("attachment_path"))
    d["attachment_basename"] = basename
    d["attachment_previewable"] = bool(basename and _is_previewable(basename))
    return jsonify(d)


@bp.get("/file/<path:basename>")
@require_admin
def serve_file(basename: str):
    """
    Authorized file serving from uploads dir, by basename only.
    """
    safe = os.path.basename(basename)  
    path = os.path.join(UPLOAD_DIR, safe)
    if not os.path.isfile(path):
        return jsonify({"error": "Not found"}), 404

  
    mimetype, _ = mimetypes.guess_type(path)
    return send_file(path, mimetype=mimetype or "application/octet-stream", as_attachment=False)



def _select_complaints_df(q: Optional[str], limit: Optional[int] = None, offset: Optional[int] = None) -> pd.DataFrame:
    conn = get_db()
    params: Dict[str, Any] = {}
    where = ""
    if q:
        where = """
        WHERE description LIKE :q
           OR complaint_number LIKE :q
           OR category LIKE :q
           OR sub_category LIKE :q
           OR full_name LIKE :q
           OR phone LIKE :q
           OR email LIKE :q
        """
        params["q"] = f"%{q}%"

    limit_sql  = " LIMIT :limit "  if limit  is not None else ""
    offset_sql = " OFFSET :offset" if offset is not None else ""

    sql = f"""
      SELECT id, complaint_number, category, sub_category, phone, email, timestamp,
             full_name, pan, address, dob, description, attachment_path
      FROM complaints
      {where}
      ORDER BY id DESC
      {limit_sql}{offset_sql}
    """
    if limit is not None:  params["limit"]  = int(limit)
    if offset is not None: params["offset"] = int(offset)

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df

@bp.get("/api/complaints/export")
@require_admin
def export_complaints():
    """
    Export complaints as CSV or XLSX.
    Query:
      q       - optional search text
      format  - csv (default) or xlsx
    """
    fmt = (request.args.get("format") or "csv").lower()
    q   = (request.args.get("q") or "").strip() or None

    df = _select_complaints_df(q=q)

    if "timestamp" in df.columns:
        pass

    if fmt == "xlsx":
        bio = io.BytesIO()
        with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="complaints")
        bio.seek(0)
        fname = "complaints.xlsx"
        return send_file(
            bio,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=fname,
        )
    else:
        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        bio = io.BytesIO(csv_bytes)
        fname = "complaints.csv"
        return send_file(
            bio,
            mimetype="text/csv",
            as_attachment=True,
            download_name=fname,
        )



@bp.post("/api/train/start")
@require_admin
def train_start():
    """
    Stub endpoint to start training asynchronously.
    You can wire this to your existing train script via subprocess if needed.
    """
    return jsonify({"ok": True, "message": "Training triggered"})


@bp.get("/api/train/status")
@require_admin
def train_status():
    """
    Stub status endpoint.
    In a real setup, read a status file or DB flag set by the training process.
    """
    return jsonify({"status": "idle"})

