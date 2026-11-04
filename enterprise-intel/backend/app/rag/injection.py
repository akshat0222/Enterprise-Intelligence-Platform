import re

PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|rules)",
    r"disregard\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier|system)",
    r"you\s+are\s+now\b",
    r"^\s*system\s*:",
    r"new\s+instructions?\s*:",
    r"reveal\s+(your\s+|the\s+)?(system\s+)?prompt",
    r"<\s*script\b",
    r"act\s+as\s+(an?\s+)?(unrestricted|jailbroken|dan)\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in PATTERNS]


def looks_like_injection(text: str) -> bool:
    """Heuristic scan for instruction-like text. Flagged chunks are surfaced in the UI and wrapped at prompt time."""
    return any(p.search(text) for p in _COMPILED)
