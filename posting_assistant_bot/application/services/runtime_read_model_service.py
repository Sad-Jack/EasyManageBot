from __future__ import annotations

from posting_assistant_bot.application.ports import ChatHistoryRepositoryPort, PendingPost, PendingPostRepositoryPort, TopicRepositoryPort


class RuntimeReadModelService:
    """Read-model/service facade for runtime handlers (status/reset/queue)."""

    def __init__(
        self,
        *,
        pending_posts: PendingPostRepositoryPort,
        topics: TopicRepositoryPort,
        chat_history: ChatHistoryRepositoryPort,
    ) -> None:
        self._pending_posts = pending_posts
        self._topics = topics
        self._chat_history = chat_history

    def clear_chat_history(self, chat_id: str) -> None:
        self._chat_history.clear_chat_history(chat_id)

    def list_pending_posts(self, chat_id: str, limit: int = 10) -> list[PendingPost]:
        return self._pending_posts.list_pending_posts(chat_id, limit)

    def pending_posts_count(self, chat_id: str, limit: int = 50) -> int:
        return len(self._pending_posts.list_pending_posts(chat_id, limit))

    def topic_memory_count(self, limit: int = 1000) -> int:
        return len(self._topics.list_recent_topic_memory(limit=limit, status="ACTIVE"))
