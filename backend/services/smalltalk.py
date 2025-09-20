# services/smalltalk.py
from __future__ import annotations
import re
from typing import Optional
from . import tone

_PATTERNS = [
    (re.compile(r"\bwho\s+are\s+you\??", re.I), lambda m: tone.say("self_intro")),
    (re.compile(r"\bhow\s+are\s+you\??", re.I), lambda m: tone.say("how_are_you")),
    (re.compile(r"^(can\s+you\s+help|help( me)?|i need help)\b", re.I), lambda m: tone.say("can_help")),
    (re.compile(r"^(ok|okay|k+|kk+|cool|fine|alright)\.?$", re.I), lambda m: tone.say("ok_reply")),
    (re.compile(r"^(thanks|thank\s+you|thx|ty)\b", re.I), lambda m: tone.say("thanks_reply")),
    (re.compile(r"^(bye|goodbye|see\s+ya)\b", re.I), lambda m: tone.say("bye_reply")),
    (re.compile(r"\b(wait|give me a sec|one sec|hold on|gimme a minute)\b", re.I), lambda m: tone.say("ack_wait")),
]

def maybe(user_msg: str) -> Optional[str]:
    s = (user_msg or "").strip()
    if not s:
        return None
    for pat, fn in _PATTERNS:
        if pat.search(s):
            return fn(None)
    return None
