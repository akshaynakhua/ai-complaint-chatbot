# services/sentiment.py
from __future__ import annotations
import re

NEG = re.compile(r"\b(angry|frustrat|fed\s*up|annoy|worst|hate|cheat|scam|not\s*working|"
                 r"no\s*response|delay|late|issue|problem|complain)\b", re.I)

def tag(text: str) -> str:
    return "neg" if NEG.search(text or "") else "neutral"