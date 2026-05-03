from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

PendingPostStatus = Literal["pending", "published"]
CommentStatus = Literal["new", "forwarded_to_review", "generating", "reply_generated", "sending", "sent", "ignored", "failed"]
PostFormat = str


@dataclass(frozen=True)
class TopicSuggestion:
    topic_title: str
    topic_angle: str
    why_write: str


@dataclass(frozen=True)
class TopicSuggestionBatch:
    topics: tuple[str, ...]


@dataclass(frozen=True)
class PendingPost:
    id: int
    owner_chat_id: str
    status: PendingPostStatus
    post_format: PostFormat
    post_type_id: str
    source_request: str
    generation_input: str
    source_message_id: int | None
    post_text: str
    target_chat_id: str
    preview_message_id: int | None = None
    published_chat_id: str | None = None
    published_message_id: str | None = None


@dataclass(frozen=True)
class Comment:
    id: int
    source_chat_id: str
    source_message_id: int
    review_chat_id: str | None
    review_message_id: int | None
    comment_author_id: str
    comment_author_name: str
    comment_text: str
    status: CommentStatus
    comment_link: str | None = None
    reply_mode: str = "bot"
    source_post_id: int | None = None
    comment_author_username: str | None = None
    generated_reply: str | None = None
    sent_message_id: int | None = None


@dataclass(frozen=True)
class TopicMemoryItem:
    id: int
    topic_hash: str
    topic_summary_compact: str
    topic_keywords_compact: str
    semantic_fingerprint: str | None
    source_topic_text: str | None = None


class PostGeneratorPort(Protocol):
    async def generate_post(
        self,
        *,
        user_message: str,
        style_prompt: str | None = None,
        post_format: PostFormat = "standard",
    ) -> "GeneratedPostLike":
        ...


class TopicGeneratorPort(Protocol):
    async def suggest_topics(
        self,
        *,
        history_context: str | None = None,
        post_format: PostFormat = "standard",
        desired_count: int = 4,
        post_type_hints: tuple[str, ...] | None = None,
    ) -> TopicSuggestionBatch:
        ...


class CommentReplyGeneratorPort(Protocol):
    async def generate_comment_reply(self, *, comment_text: str) -> str:
        ...


class GeneratedPostLike(Protocol):
    post_text: str


class SuggestedTopicPort(Protocol):
    id: int
    owner_chat_id: str
    topic_title: str
    topic_angle: str
    why_write: str
    post_format: PostFormat
    post_type_id: str


class PendingPostRepositoryPort(Protocol):
    def create_pending_post(
        self,
        *,
        owner_chat_id: str,
        post_format: PostFormat,
        post_type_id: str,
        source_request: str,
        generation_input: str,
        source_message_id: int | None,
        post_text: str,
        target_chat_id: str,
    ) -> PendingPost:
        ...

    def get_pending_post_by_id(self, pending_post_id: int) -> PendingPost | None:
        ...

    def list_pending_posts(self, owner_chat_id: str, limit: int = 10) -> list[PendingPost]:
        ...

    def attach_preview_message(self, pending_post_id: int, preview_message_id: int) -> PendingPost | None:
        ...

    def mark_pending_post_published(
        self,
        pending_post_id: int,
        *,
        published_chat_id: str,
        published_message_id: str,
    ) -> PendingPost | None:
        ...

    def update_pending_post_text(self, pending_post_id: int, post_text: str) -> PendingPost | None:
        ...

    def delete_pending_post(self, pending_post_id: int) -> None:
        ...


class ChatHistoryRepositoryPort(Protocol):
    def clear_chat_history(self, chat_id: str) -> None:
        ...


class TopicRepositoryPort(Protocol):
    def create_suggested_topic(
        self,
        *,
        owner_chat_id: str,
        owner_id: str,
        topic_title: str,
        topic_angle: str,
        why_write: str,
        source: Literal["manual_command", "panel_button", "regenerate"],
        post_format: PostFormat = "standard",
        post_type_id: str = "educational_short",
        topic_message_id: int | None = None,
    ) -> SuggestedTopicPort:
        ...

    def get_suggested_topic_by_id(self, topic_id: int) -> SuggestedTopicPort | None:
        ...

    def update_suggested_topic_content(
        self,
        topic_id: int,
        *,
        topic_title: str,
        topic_angle: str,
        why_write: str,
        source: Literal["manual_command", "panel_button", "regenerate"],
        post_format: PostFormat = "standard",
        post_type_id: str = "educational_short",
    ) -> SuggestedTopicPort | None:
        ...

    def attach_topic_message(self, topic_id: int, topic_message_id: int) -> SuggestedTopicPort | None:
        ...

    def mark_suggested_topic_status(
        self,
        topic_id: int,
        status: Literal["suggested", "post_generated", "deleted"],
    ) -> SuggestedTopicPort | None:
        ...

    def create_or_touch_topic_memory(
        self,
        *,
        source_topic_text: str | None,
        topic_hash: str,
        topic_summary_compact: str,
        topic_keywords_compact: str,
        semantic_fingerprint: str | None,
        post_id: int | None,
        message_id: int | None,
        status: Literal["ACTIVE", "ARCHIVED"] = "ACTIVE",
    ) -> tuple[TopicMemoryItem, bool]:
        ...

    def list_recent_topic_memory(
        self,
        *,
        limit: int = 100,
        status: Literal["ACTIVE", "ARCHIVED"] = "ACTIVE",
    ) -> list[TopicMemoryItem]:
        ...


class CommentRepositoryPort(Protocol):
    def create_comment(
        self,
        *,
        source_chat_id: str,
        source_message_id: int,
        source_post_id: int | None,
        comment_author_id: str,
        comment_author_username: str | None,
        comment_author_name: str,
        comment_text: str,
        comment_link: str | None,
        reply_mode: str,
    ) -> Comment:
        ...

    def get_comment_by_id(self, comment_id: int) -> Comment | None:
        ...

    def update_comment_review_message(
        self,
        comment_id: int,
        *,
        review_chat_id: str,
        review_message_id: int,
    ) -> Comment | None:
        ...

    def mark_comment_status(self, comment_id: int, status: CommentStatus) -> Comment | None:
        ...

    def update_comment_generated_reply(self, comment_id: int, reply_text: str) -> Comment | None:
        ...

    def mark_comment_sent(self, comment_id: int, sent_message_id: int) -> Comment | None:
        ...

    def acquire_comment_send_lock(self, comment_id: int) -> Comment | None:
        ...
