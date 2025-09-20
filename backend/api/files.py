import os
from flask import Blueprint, send_from_directory
from services import UPLOAD_DIR

bp = Blueprint("files", __name__)

@bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)
