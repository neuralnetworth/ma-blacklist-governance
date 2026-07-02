"""Redaction and markdown-safety helpers."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


REDACTED = "[REDACTED]"

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|account[_-]?id)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{6,})"),
    re.compile(r"/(?:Users|home)/[^\s)'\"<>]+"),
]


def redact_text(text: str, secrets: Sequence[str] | None = None) -> str:
    """Redact known secret values, API-token shapes, and private local paths."""

    out = text
    for secret in secrets or ():
        if secret and len(secret) >= 4:
            out = out.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: f"{m.group(1)}={REDACTED}" if m.groups() else REDACTED, out)
    return out


def redact_data(data: Any, secrets: Sequence[str] | None = None) -> Any:
    """Recursively redact strings inside a JSON-like value."""

    if isinstance(data, str):
        return redact_text(data, secrets)
    if isinstance(data, Mapping):
        return {k: redact_data(v, secrets) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_data(v, secrets) for v in data]
    return data


def redacted_json(data: Any, secrets: Sequence[str] | None = None) -> str:
    """Serialize JSON-like data after recursive redaction."""

    return json.dumps(redact_data(data, secrets), indent=2, sort_keys=True) + "\n"


def sanitize_markdown_evidence(text: str, secrets: Sequence[str] | None = None) -> str:
    """Render untrusted evidence as inert markdown text."""

    redacted = redact_text(text or "", secrets)
    escaped = html.escape(redacted, quote=False)
    escaped = escaped.replace("`", "'")
    escaped = escaped.replace("[", "\\[").replace("]", "\\]")
    escaped = escaped.replace("(", "\\(").replace(")", "\\)")
    escaped = escaped.replace("!", "\\!")
    return escaped
