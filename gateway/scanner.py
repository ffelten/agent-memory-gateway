"""Bounded local admission checks; source permissions carry PII protection."""
import re

_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r'DEMO_SECRET_[A-Z0-9_]+',
    r'\bbearer\s+[A-Za-z0-9._~+/=-]{6,}',
    r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    r'\b(?:api[_-]?key|secret|token|password)["\x27]?\s*[:=]\s*["\x27]?[A-Za-z0-9._~+/=-]{6,}',
    r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',
))


def has_secret(title, text):
    return any(pattern.search(value) for pattern in _PATTERNS for value in (title, text))
