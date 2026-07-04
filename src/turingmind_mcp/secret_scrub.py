"""Scrub common secret patterns before cloud sync or external export."""

from __future__ import annotations

import re

SECRET_REGEX = re.compile(
    r"(?i)(bearer\s+[a-z0-9_\-\.]+)"
    r"|(sk-[a-zA-Z0-9]{20,})"
    r"|(AKIA[0-9A-Z]{16})"
    r"|(xox[baprs]-[0-9a-zA-Z]{10,})"
)


def scrub_secrets(text: str | None) -> str | None:
    """Replace likely secrets in free text. Returns input unchanged when empty."""
    if not text:
        return text
    return SECRET_REGEX.sub("[REDACTED_SECRET]", text)
