from __future__ import annotations

from posting_assistant_bot.application.ports import Comment, CommentReplyGeneratorPort, CommentRepositoryPort, CommentStatus


class CommentsService:
    """Use-case сервис для жизненного цикла комментария и ответа на него."""

    def __init__(
        self,
        *,
        comments: CommentRepositoryPort,
        reply_generator: CommentReplyGeneratorPort,
    ) -> None:
        self._comments = comments
        self._reply_generator = reply_generator

    def create_comment_notification_state(
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
        return self._comments.create_comment(
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            source_post_id=source_post_id,
            comment_author_id=comment_author_id,
            comment_author_username=comment_author_username,
            comment_author_name=comment_author_name,
            comment_text=comment_text,
            comment_link=comment_link,
            reply_mode=reply_mode,
        )

    def get_comment(self, comment_id: int) -> Comment | None:
        return self._comments.get_comment_by_id(comment_id)

    def update_review_message_state(
        self,
        comment_id: int,
        *,
        review_chat_id: str,
        review_message_id: int,
    ) -> Comment | None:
        return self._comments.update_comment_review_message(
            comment_id,
            review_chat_id=review_chat_id,
            review_message_id=review_message_id,
        )

    async def generate_comment_reply(self, comment_id: int) -> Comment | None:
        comment = self._comments.get_comment_by_id(comment_id)
        if comment is None:
            return None
        self._comments.mark_comment_status(comment_id, "generating")
        try:
            reply_text = await self._reply_generator.generate_comment_reply(comment_text=comment.comment_text)
        except Exception:
            self._comments.mark_comment_status(comment_id, "failed")
            raise
        return self._comments.update_comment_generated_reply(comment_id, reply_text)

    def mark_comment_sent(self, comment_id: int, sent_message_id: int) -> Comment | None:
        return self._comments.mark_comment_sent(comment_id, sent_message_id)

    def acquire_comment_send_lock(self, comment_id: int) -> Comment | None:
        return self._comments.acquire_comment_send_lock(comment_id)

    def mark_comment_status(self, comment_id: int, status: CommentStatus) -> Comment | None:
        return self._comments.mark_comment_status(comment_id, status)

    @staticmethod
    def render_comment_review_message(comment: Comment, generated_reply: str | None = None) -> str:
        username = f"@{comment.comment_author_username}" if comment.comment_author_username else "—"
        link = comment.comment_link or "—"
        header = "💬 Необработано" if not generated_reply else "💬 Ответ сгенерирован"
        lines = [
            header,
            "",
            f"Автор: {comment.comment_author_name}",
            f"Username: {username}",
            f"Ссылка: {link}",
            "",
            "Комментарий:",
            comment.comment_text,
        ]
        if generated_reply:
            lines.extend(["", "Ответ:", generated_reply])
        return "\n".join(lines)

    @staticmethod
    def render_comment_sent_message(comment: Comment) -> str:
        return "\n".join(
            [
                "✅ Обработано",
                "",
                f"Автор: {comment.comment_author_name}",
                f"Ссылка: {comment.comment_link or '—'}",
                "",
                "Ответ отправлен.",
            ]
        )

    @staticmethod
    def render_comment_generating_message(comment: Comment) -> str:
        username = f"@{comment.comment_author_username}" if comment.comment_author_username else "—"
        link = comment.comment_link or "—"
        return "\n".join(
            [
                "⏳ Генерирую ответ",
                "",
                f"Автор: {comment.comment_author_name}",
                f"Username: {username}",
                f"Ссылка: {link}",
                "",
                "Комментарий:",
                comment.comment_text,
            ]
        )
