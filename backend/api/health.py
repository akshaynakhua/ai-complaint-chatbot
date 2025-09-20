from flask import Blueprint, jsonify
from services.extractors import EXTRACTORS_INFO

bp = Blueprint("health", __name__)

@bp.get("/health")
def health():
    return jsonify({"ok": True, "extractors": EXTRACTORS_INFO})
