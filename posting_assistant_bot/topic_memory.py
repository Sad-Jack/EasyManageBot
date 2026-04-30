from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from posting_assistant_bot.application.ports import TopicMemoryItem

_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-Я0-9]+")
_SPACE_RE = re.compile(r"\s+")

_STOPWORDS = {
    "и",
    "в",
    "на",
    "по",
    "как",
    "что",
    "это",
    "для",
    "про",
    "или",
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
}


@dataclass(frozen=True)
class TopicMemoryPayload:
    source_topic_text: str
    topic_hash: str
    topic_summary_compact: str
    topic_keywords_compact: str
    semantic_fingerprint: str


def normalize_topic_text(value: str) -> str:
    text = value.strip().lower()
    text = _SPACE_RE.sub(" ", text)
    return text


def extract_tokens(value: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(value)]


def compact_topic_keywords(value: str, *, limit: int = 8) -> str:
    seen: set[str] = set()
    selected: list[str] = []
    for token in extract_tokens(value):
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        selected.append(token)
        if len(selected) >= limit:
            break
    return ", ".join(selected)


def compact_topic_summary(value: str, *, limit: int = 120) -> str:
    normalized = normalize_topic_text(value)
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def semantic_fingerprint(value: str, *, limit: int = 12) -> str:
    unique = sorted({token for token in extract_tokens(value) if len(token) >= 3 and token not in _STOPWORDS})
    return "|".join(unique[:limit])


def topic_hash(value: str) -> str:
    normalized = normalize_topic_text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_topic_memory_payload(value: str) -> TopicMemoryPayload:
    normalized = normalize_topic_text(value)
    return TopicMemoryPayload(
        source_topic_text=normalized,
        topic_hash=topic_hash(normalized),
        topic_summary_compact=compact_topic_summary(normalized),
        topic_keywords_compact=compact_topic_keywords(normalized),
        semantic_fingerprint=semantic_fingerprint(normalized),
    )


def jaccard_similarity(left: str, right: str) -> float:
    left_tokens = {token for token in extract_tokens(left) if len(token) >= 3 and token not in _STOPWORDS}
    right_tokens = {token for token in extract_tokens(right) if len(token) >= 3 and token not in _STOPWORDS}
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = left_tokens.intersection(right_tokens)
    union = left_tokens.union(right_tokens)
    return len(intersection) / len(union)


def is_near_duplicate(candidate: str, memory: list[TopicMemoryItem], *, threshold: float = 0.72) -> bool:
    for item in memory:
        similarity = jaccard_similarity(candidate, item.topic_summary_compact)
        if similarity >= threshold:
            return True
    return False


def filter_duplicate_topics(
    candidates: list[str],
    memory: list[TopicMemoryItem],
    *,
    limit: int = 4,
    threshold: float = 0.72,
) -> list[str]:
    selected: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        normalized = normalize_topic_text(candidate)
        if is_near_duplicate(normalized, memory, threshold=threshold):
            continue
        if any(jaccard_similarity(normalized, existing) >= threshold for existing in selected):
            continue
        selected.append(candidate.strip())
        if len(selected) >= limit:
            break
    return selected


def build_history_context(memory: list[TopicMemoryItem], *, max_items: int = 40, max_chars: int = 1600) -> str:
    if not memory:
        return ""
    lines: list[str] = []
    for item in memory[:max_items]:
        summary = item.topic_summary_compact.strip()
        keywords = item.topic_keywords_compact.strip()
        if summary and keywords:
            lines.append(f"- {summary} | ключи: {keywords}")
        elif summary:
            lines.append(f"- {summary}")
    result = "\n".join(lines)
    if len(result) <= max_chars:
        return result
    return f"{result[:max_chars].rstrip()}\n..."
