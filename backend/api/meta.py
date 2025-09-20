# backend/api/meta.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
import services.registries_service as reg  # single import; functions accessed via _safe()

# ❗ Use a UNIQUE internal blueprint name to avoid collisions (was "meta")
bp = Blueprint("meta_api", __name__)  # public URL prefix is still set in app.py via url_prefix="/meta"

def _safe(func_name: str):
    """
    Return a registry function from services.registries_service if it exists,
    else a no-op that returns [].
    """
    return getattr(reg, func_name, lambda *a, **k: [])

@bp.get("/brokers/suggest")
def brokers_suggest():
    q = (request.args.get("q") or "").strip()
    broker_candidates = _safe("broker_candidates")
    items = [{"name": n} for n in (broker_candidates(q) if q else [])]
    return jsonify({"items": items})

@bp.get("/exchanges/suggest")
def exchanges_suggest():
    q = (request.args.get("q") or "").strip()
    exchange_suggestions = _safe("exchange_suggestions")
    items = [{"name": n} for n in (exchange_suggestions(q) if q else [])]
    return jsonify({"items": items})

@bp.get("/companies/suggest")
def companies_suggest():
    q = (request.args.get("q") or "").strip()
    company_candidates = _safe("company_candidates")
    items = [{"name": n} for n in (company_candidates(q) if q else [])]
    return jsonify({"items": items})

@bp.get("/mutualfunds/suggest")
def mutualfunds_suggest():
    q = (request.args.get("q") or "").strip()
    mutualfund_candidates = _safe("mutualfund_candidates")
    items = [{"name": n} for n in (mutualfund_candidates(q) if q else [])]
    return jsonify({"items": items})

@bp.get("/advisers/suggest")
def advisers_suggest():
    q = (request.args.get("q") or "").strip()
    advisor_candidates = _safe("advisor_candidates")
    items = [{"name": n} for n in (advisor_candidates(q) if q else [])]
    return jsonify({"items": items})
