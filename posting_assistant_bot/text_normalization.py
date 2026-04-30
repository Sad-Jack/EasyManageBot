from __future__ import annotations

import re


def normalize_markdown_for_telegram(text: str) -> str:
    normalized = text
    patterns = (
        (r"\*\*([^*\n]+)\*\*", r"\1"),
        (r"__([^_\n]+)__", r"\1"),
        (r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1"),
        (r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1"),
    )

    for _ in range(4):
        previous = normalized
        for pattern, replacement in patterns:
            normalized = re.sub(pattern, replacement, normalized)
        if normalized == previous:
            break
    return normalized

