from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DraftGenerated:
    draft_id: int
    owner_chat_id: str
    source_message_id: int | None
    occurred_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class DraftPublished:
    draft_id: int
    target_chat_id: str
    published_message_id: str
    occurred_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class DraftDeleted:
    draft_id: int
    reason: str | None = None
    occurred_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class CommentReplyGenerated:
    comment_id: int
    reply_text: str
    occurred_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class CommentReplySent:
    comment_id: int
    source_chat_id: str
    sent_message_id: int
    occurred_at: datetime = field(default_factory=_utc_now)

