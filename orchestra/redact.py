"""Secret scrubbing. Applied at the serialization boundary so no path bypasses it."""

import re
from typing import Any, List, Optional, Pattern, Tuple

_MARK = "<redacted:{}>"

# Order matters: the more specific pattern must come first, or the generic one
# eats its prefix and the label is wrong.
_PATTERNS: List[Tuple[str, Pattern]] = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
]

# Handled separately: only the value is replaced, so the key name stays readable.
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
    r"(\s*[=:]\s*)(['\"]?)([^\s'\"]{8,})(['\"]?)")


def _assignment_repl(m: "re.Match") -> str:
    return "{}{}{}{}{}".format(m.group(1), m.group(2), m.group(3),
                               _MARK.format("secret"), m.group(5))


def scrub(text: Optional[str]) -> Optional[str]:
    """Replace anything that looks like a credential with a labelled marker."""
    if not text:
        return text
    for label, pattern in _PATTERNS:
        text = pattern.sub(_MARK.format(label), text)
    return _ASSIGNMENT.sub(_assignment_repl, text)


def scrub_obj(obj: Any) -> Any:
    """Recurse through dicts, lists, and strings. Other types pass through."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {k: scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(v) for v in obj]
    return obj
