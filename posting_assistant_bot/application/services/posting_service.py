from __future__ import annotations

from posting_assistant_bot.application.ports import PendingPost, PendingPostRepositoryPort, PostFormat, PostGeneratorPort
from posting_assistant_bot.text_normalization import normalize_markdown_for_telegram


class PostingService:
    """Use-case сервис для генерации и жизненного цикла pending-постов."""

    def __init__(
        self,
        *,
        post_generator: PostGeneratorPort,
        pending_posts: PendingPostRepositoryPort,
        target_chat_id: str,
    ) -> None:
        self._post_generator = post_generator
        self._pending_posts = pending_posts
        self._target_chat_id = target_chat_id

    async def generate_pending_post(
        self,
        *,
        owner_chat_id: str,
        post_format: PostFormat,
        post_type_id: str,
        source_request: str,
        generation_input: str,
        source_message_id: int | None,
        style_prompt: str | None = None,
    ) -> PendingPost:
        generated = await self._post_generator.generate_post(
            user_message=generation_input,
            style_prompt=style_prompt,
            post_format=post_format,
        )
        normalized_post_text = normalize_markdown_for_telegram(generated.post_text)
        return self._pending_posts.create_pending_post(
            owner_chat_id=owner_chat_id,
            post_format=post_format,
            post_type_id=post_type_id,
            source_request=source_request,
            generation_input=generation_input,
            source_message_id=source_message_id,
            post_text=normalized_post_text,
            target_chat_id=self._target_chat_id,
        )

    def get_pending_post(self, pending_post_id: int) -> PendingPost | None:
        return self._pending_posts.get_pending_post_by_id(pending_post_id)

    def attach_preview_message(self, pending_post_id: int, preview_message_id: int) -> PendingPost | None:
        return self._pending_posts.attach_preview_message(pending_post_id, preview_message_id)

    def mark_pending_post_published(
        self,
        pending_post_id: int,
        *,
        published_chat_id: str,
        published_message_id: str,
    ) -> PendingPost | None:
        return self._pending_posts.mark_pending_post_published(
            pending_post_id,
            published_chat_id=published_chat_id,
            published_message_id=published_message_id,
        )

    async def reopen_pending_post(self, pending_post_id: int, *, style_prompt: str | None = None) -> PendingPost | None:
        pending = self._pending_posts.get_pending_post_by_id(pending_post_id)
        if pending is None:
            return None
        generated = await self._post_generator.generate_post(
            user_message=pending.generation_input,
            style_prompt=style_prompt,
            post_format=pending.post_format,
        )
        normalized_post_text = normalize_markdown_for_telegram(generated.post_text)
        return self._pending_posts.update_pending_post_text(pending_post_id, normalized_post_text)
