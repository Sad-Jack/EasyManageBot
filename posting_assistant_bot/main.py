from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

from aiohttp import web
from telegram import BotCommand, Message, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Conflict, Forbidden, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from posting_assistant_bot.application.ports import PendingPost, PostFormat
from posting_assistant_bot.application.services import CommentsService, PostingService, TopicsService
from posting_assistant_bot.claude.orchestrator import PostingAssistantOrchestrator
from posting_assistant_bot.config import AppConfig, load_config
from posting_assistant_bot.logging_utils import log_event, setup_logging
from posting_assistant_bot.obsidian.vault import ObsidianVault
from posting_assistant_bot.storage.database import AppDatabase
from posting_assistant_bot.telegram_ui import (
    bot_command_entries,
    build_comment_generate_keyboard,
    build_comment_reply_keyboard,
    build_control_panel_keyboard,
    build_pending_post_keyboard,
    build_post_link_keyboard,
    build_topic_selection_keyboard,
    render_control_panel_text,
)
from posting_assistant_bot.text_normalization import normalize_markdown_for_telegram
from posting_assistant_bot.transcription import VoiceTranscriber
from posting_assistant_bot.ui_text import UITexts, get_ui_texts

LOGGER = logging.getLogger(__name__)


class PostingAssistantBotRuntime:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._ui: UITexts = get_ui_texts(config.ui_language)
        self._db = AppDatabase(config.sqlite_path)
        self._vault = ObsidianVault(config.obsidian_vault_path)
        self._orchestrator = PostingAssistantOrchestrator(config, self._vault)
        self._posting_service = PostingService(
            post_generator=self._orchestrator,
            pending_posts=self._db,
            target_chat_id=self._config.target_chat_id,
        )
        self._topics_service = TopicsService(topic_generator=self._orchestrator, topics=self._db, config=self._config)
        self._comments_service = CommentsService(comments=self._db, reply_generator=self._orchestrator)
        self._transcriber = VoiceTranscriber(
            model=config.local_transcribe_model,
            enabled=config.voice_transcription_enabled,
            provider=config.voice_transcription_provider,
        )
        self._comment_generation_tasks: dict[int, asyncio.Task[None]] = {}
        self._audio_temp_dir = self._prepare_audio_temp_dir(config.voice_tmp_dir)
        self._publish_ready, self._publish_not_ready_reason = _validate_target_chat_id(config.target_chat_id)
        self._application = (
            ApplicationBuilder()
            .token(config.telegram_bot_token)
            .concurrent_updates(8)
            .build()
        )
        self._register_handlers()

    def run(self) -> None:
        log_event(
            LOGGER,
            level=logging.INFO,
            component="bot.runtime",
            event="bot_startup",
            message="Bot startup sequence started",
            context={
                "mode": self._config.bot_mode,
                "target_chat_id": self._config.target_chat_id,
                "target_chat_id_source": self._config.target_chat_id_source,
                "topic_generation_model": self._config.topic_generation_model,
                "publish_ready": self._publish_ready,
                "publish_not_ready_reason": self._publish_not_ready_reason,
                "owner_telegram_id": self._config.owner_telegram_id,
                "transcription_enabled": self._transcriber.is_configured(),
                "transcription_ready": self._transcriber.is_ready(),
                "transcription_not_ready_reason": self._transcriber.not_ready_reason(),
                "transcription_provider": self._config.voice_transcription_provider,
                "voice_max_duration_seconds": self._config.voice_max_duration_seconds,
                "voice_tmp_dir": self._config.voice_tmp_dir,
                "audio_temp_dir": str(self._audio_temp_dir),
                "obsidian_enabled": self._vault.is_configured(),
                "post_types_config_path": self._config.post_types_config_path,
                "active_post_types": [item.id for item in self._config.post_types],
                "topic_layout": {
                    "topic_count": self._config.topic_layout.topic_count,
                    "buttons_per_row": self._config.topic_layout.buttons_per_row,
                    "candidate_count": self._config.topic_layout.candidate_count,
                },
                "log_level": self._config.log_level,
                "log_format": self._config.log_format,
                "log_to_file": self._config.log_to_file,
                "log_file_path": self._config.log_file_path,
            },
        )
        if not self._transcriber.is_ready():
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="bot.startup",
                event="voice_not_ready",
                message="Voice transcription is not ready",
                context={"reason": self._transcriber.not_ready_reason()},
            )
        if not self._publish_ready:
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="bot.startup",
                event="publish_not_ready",
                message="Publish target chat is not ready",
                context={"target_chat_id": self._config.target_chat_id, "reason": self._publish_not_ready_reason},
            )
        if self._config.bot_mode == "webhook":
            self._run_webhook()
            return

        log_event(
            LOGGER,
            level=logging.INFO,
            component="bot.runtime",
            event="bot_started",
            message="Starting Posting Assistant bot in polling mode",
            context={"mode": "polling"},
        )
        try:
            self._application.post_init = self._post_init
            self._application.run_polling()
        except Conflict:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="bot.polling",
                event="duplicate_instance",
                message="Another bot instance is already running. Stop duplicate process or disable webhook.",
            )
        finally:
            self._cancel_background_tasks()
            self._cleanup_audio_temp_dir()
            self._db.close()

    def _register_handlers(self) -> None:
        self._application.add_handler(CommandHandler("start", self._handle_start))
        self._application.add_handler(CommandHandler("panel", self._handle_panel))
        self._application.add_handler(CommandHandler("help", self._handle_panel))
        self._application.add_handler(CommandHandler("topic", self._handle_topic))
        self._application.add_handler(CommandHandler("reset", self._handle_reset))
        self._application.add_handler(CommandHandler("status", self._handle_status))
        self._application.add_handler(CommandHandler("queue", self._handle_queue))
        self._application.add_handler(CallbackQueryHandler(self._handle_callback_query))
        self._application.add_handler(
            MessageHandler(
                filters.TEXT & filters.Regex(f"^{re.escape(self._ui.topic_button)}$"),
                self._handle_topic_button,
            )
        )
        self._application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, self._handle_comment_message)
        )
        self._application.add_handler(MessageHandler(filters.VOICE, self._handle_voice_message))
        self._application.add_handler(MessageHandler(filters.AUDIO, self._handle_audio_message))
        self._application.add_handler(MessageHandler(filters.VIDEO_NOTE, self._handle_video_note_message))
        self._application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text_message))
        self._application.add_error_handler(self._handle_error)

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return
        self._log_command(update, "start")

        message = self._reply_target(update)
        if message is None:
            return
        await message.reply_text(
            "\n".join(
                [
                    self._ui.ready_line_1,
                    self._ui.ready_line_2,
                    self._ui.ready_line_3,
                    "",
                    self._ui.menu_buttons_header,
                    self._ui.menu_topic_desc,
                ]
            ),
            reply_markup=build_control_panel_keyboard(self._ui),
            disable_web_page_preview=True,
        )

    async def _handle_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return
        self._log_command(update, "topic")
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        await self._send_topic_suggestions(
            message=message,
            chat_id=str(chat.id),
            source="manual_command",
        )

    async def _handle_topic_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        log_event(
            LOGGER,
            level=logging.INFO,
            component="telegram.command",
            event="topic_button_clicked",
            message="Topics generator button clicked",
            context={"chat_id": chat.id, "message_id": message.message_id},
        )
        await self._send_topic_suggestions(
            message=message,
            chat_id=str(chat.id),
            source="panel_button",
        )

    async def _handle_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return
        self._log_command(update, "panel")

        message = self._reply_target(update)
        if message is None:
            return

        await message.reply_text(
            render_control_panel_text(self._ui),
            reply_markup=build_control_panel_keyboard(self._ui),
            disable_web_page_preview=True,
        )

    async def _handle_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return
        self._log_command(update, "reset")

        chat = update.effective_chat
        if chat is None:
            return

        self._db.clear_chat_history(str(chat.id))
        await self._reply_text(update, "Служебная история этого чата очищена.")

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return
        self._log_command(update, "status")

        chat = update.effective_chat
        pending_count = 0
        topic_memory_count = 0
        if chat is not None:
            pending_count = len(self._db.list_pending_posts(str(chat.id), 50))
        topic_memory_count = len(self._db.list_recent_topic_memory(limit=1000, status="ACTIVE"))

        summary = "\n".join(
            [
                f"Model: {self._config.claude_code_model}",
                f"Topic model: {self._config.topic_generation_model}",
                f"Owner lock: enabled ({self._config.owner_telegram_id})",
                f"Target chat: {self._config.target_chat_id}",
                f"Target chat source: {self._config.target_chat_id_source}",
                f"Publish readiness: {'ready' if self._publish_ready else f'not_ready: {self._publish_not_ready_reason}'}",
                f"Voice transcription: {'enabled' if self._config.voice_transcription_enabled else 'disabled'} "
                f"({self._config.voice_transcription_provider}, {self._config.local_transcribe_model})",
                f"Voice readiness: {self._transcriber.readiness_summary()}",
                f"Voice max duration sec: {self._config.voice_max_duration_seconds}",
                f"Obsidian: {'configured' if self._vault.is_configured() else 'not configured'}",
                f"Role prompt file: {self._config.role_prompt_path}",
                f"Style prompt file: {self._config.style_prompt_path}",
                f"Themes prompt file: {self._config.themes_prompt_path}",
                f"Comment source chat: {self._config.comment_source_chat_id or 'not configured'}",
                f"Comment review chat: {self._config.comment_review_chat_id or 'not configured'}",
                f"Comment reply mode: {self._config.comment_reply_mode}",
                f"UI language: {self._config.ui_language}",
                f"Active post types: {len(self._config.post_types)}",
                f"Topic layout: count={self._config.topic_layout.topic_count}, row={self._config.topic_layout.buttons_per_row}",
                f"Tag catalog: {self._config.tag_catalog_path}",
                f"Pending posts in this chat: {pending_count}",
                f"Topic memory entries: {topic_memory_count}",
            ]
        )
        await self._reply_text(update, summary)

    async def _handle_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return
        self._log_command(update, "queue")

        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return

        posts = self._db.list_pending_posts(str(chat.id), 10)
        if not posts:
            await message.reply_text("Неопубликованных постов пока нет.", disable_web_page_preview=True)
            return

        for post in posts:
            await message.reply_text(self._render_queue_item(post), disable_web_page_preview=True)

    async def _handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not message.text:
            return
        if self._is_comment_source_chat(chat.id):
            return

        text = message.text.strip()
        if not text or text.startswith("/"):
            return

        chat_id = str(chat.id)
        log_event(
            LOGGER,
            level=logging.INFO,
            component="telegram.text_handler",
            event="text_message_received",
            message="Text message received from owner",
            context={
                "user_id": message.from_user.id if message.from_user else None,
                "chat_id": chat.id,
                "message_id": message.message_id,
                "text_length": len(text),
            },
        )

        await self._generate_and_preview_post(
            message=message,
            chat_id=chat_id,
            source_request=text,
            generation_input=text,
            post_format="standard",
            post_type_id="educational_short",
            context=context,
        )

    async def _handle_comment_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None or not message.text:
            return

        if not self._is_comment_source_chat(chat.id):
            return

        if message.from_user is None:
            return

        if message.from_user.is_bot:
            return

        if str(message.from_user.id) == str(self._config.owner_telegram_id):
            return

        if await self._is_chat_admin(chat.id, message.from_user.id):
            log_event(
                LOGGER,
                level=logging.INFO,
                component="comments.flow",
                event="comment_ignored_admin",
                message="Comment ignored because author is chat admin",
                context={"chat_id": chat.id, "message_id": message.message_id, "author_id": message.from_user.id},
            )
            return

        if self._is_filtered_chat_post(message):
            log_event(
                LOGGER,
                level=logging.INFO,
                component="comments.flow",
                event="comment_ignored_chat_post",
                message="Comment ignored because message is channel/auto-forward post",
                context={"chat_id": chat.id, "message_id": message.message_id},
            )
            return

        text = message.text.strip()
        if not text:
            log_event(
                LOGGER,
                level=logging.INFO,
                component="telegram.comments",
                event="comment_ignored_empty_text",
                message="Comment ignored due to empty text",
                context={"chat_id": chat.id, "message_id": message.message_id},
            )
            return

        if not self._config.comment_review_chat_id:
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="comments.flow",
                event="review_chat_not_configured",
                message="Comment review chat is not configured",
                context={"source_chat_id": chat.id},
            )
            return

        comment_link = self._build_message_url(chat.username, chat.id, message.message_id)
        log_event(
            LOGGER,
            level=logging.INFO,
            component="comments.flow",
            event="comment_received",
            message="Comment received",
            context={
                "chat_id": chat.id,
                "message_id": message.message_id,
                "author_id": message.from_user.id,
                "text_length": len(text),
            },
        )
        try:
            record = self._comments_service.create_comment_notification_state(
                source_chat_id=str(chat.id),
                source_message_id=message.message_id,
                source_post_id=message.reply_to_message.message_id if message.reply_to_message else None,
                comment_author_id=str(message.from_user.id),
                comment_author_username=message.from_user.username,
                comment_author_name=message.from_user.full_name,
                comment_text=text,
                comment_link=comment_link,
                reply_mode=self._config.comment_reply_mode,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="comments.flow",
                event="comment_save_failed",
                message="Failed to save comment",
                context={"chat_id": chat.id, "message_id": message.message_id, "error_message": str(exc)},
                exc_info=exc,
            )
            return

        try:
            review_message = await self._application.bot.send_message(
                chat_id=int(self._config.comment_review_chat_id),
                text=self._comments_service.render_comment_review_message(record),
                reply_markup=build_comment_generate_keyboard(record.id),
                disable_web_page_preview=True,
            )
            self._comments_service.update_review_message_state(
                record.id,
                review_chat_id=self._config.comment_review_chat_id,
                review_message_id=review_message.message_id,
            )
            log_event(
                LOGGER,
                level=logging.INFO,
                component="comments.flow",
                event="comment_forwarded_to_review",
                message="Comment forwarded to review chat",
                context={"comment_id": record.id, "review_message_id": review_message.message_id},
            )
        except Exception as exc:
            self._comments_service.mark_comment_status(record.id, "failed")
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="comments.flow",
                event="comment_forward_failed",
                message="Failed to forward comment to review chat",
                context={"comment_id": record.id, "error_message": str(exc)},
                exc_info=exc,
            )

    async def _send_topic_suggestions(
        self,
        *,
        message: Message,
        chat_id: str,
        source: str,
    ) -> None:
        try:
            await self._application.bot.send_chat_action(chat_id=int(chat_id), action=ChatAction.TYPING)
            records = await self._topics_service.suggest_topics(
                owner_chat_id=chat_id,
                owner_id=str(message.from_user.id if message.from_user else ""),
                source=source,
            )
            topic_text = self._topics_service.render_topics_message([item.topic_title for item in records], self._ui)
            sent = await message.reply_text(
                topic_text,
                disable_web_page_preview=True,
                reply_markup=build_topic_selection_keyboard(
                    [item.id for item in records],
                    buttons_per_row=self._config.topic_layout.buttons_per_row,
                    max_buttons=self._config.topic_layout.topic_count,
                ),
            )
            for record in records:
                self._topics_service.attach_topic_message(record.id, sent.message_id)
            log_event(
                LOGGER,
                level=logging.INFO,
                component="topic.generator",
                event="topics_generated",
                message="Generated topics",
                context={
                    "chat_id": chat_id,
                    "source": source,
                    "topics_count": len(records),
                    "buttons_per_row": self._config.topic_layout.buttons_per_row,
                },
            )
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="topic.generator",
                event="topic_generation_failed",
                message="Failed to generate topics",
                context={
                    "chat_id": chat_id,
                    "source": source,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            await message.reply_text("Не удалось сгенерировать темы. Попробуй ещё раз.")

    async def _handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or message.voice is None:
            return
        await self._handle_transcribable_media(
            update=update,
            context=context,
            file_id=message.voice.file_id,
            duration_seconds=message.voice.duration,
            source_kind="voice",
        )

    async def _handle_audio_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or message.audio is None:
            return
        await self._handle_transcribable_media(
            update=update,
            context=context,
            file_id=message.audio.file_id,
            duration_seconds=message.audio.duration or 0,
            source_kind="audio",
        )

    async def _handle_video_note_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or message.video_note is None:
            return
        await self._handle_transcribable_media(
            update=update,
            context=context,
            file_id=message.video_note.file_id,
            duration_seconds=message.video_note.duration,
            source_kind="video_note",
        )

    async def _handle_transcribable_media(
        self,
        *,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        file_id: str,
        duration_seconds: int,
        source_kind: str,
    ) -> None:
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            return

        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        if not self._transcriber.is_ready():
            reason = self._transcriber.not_ready_reason() or "voice_not_ready"
            await message.reply_text(self._map_runtime_error_to_user_message(RuntimeError(reason)))
            return
        if duration_seconds > self._config.voice_max_duration_seconds:
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="telegram.voice_handler",
                event="voice_duration_limit_exceeded",
                message="Voice message duration exceeds configured limit",
                context={
                    "chat_id": chat.id,
                    "message_id": message.message_id,
                    "duration": duration_seconds,
                    "max_duration": self._config.voice_max_duration_seconds,
                    "source_kind": source_kind,
                },
            )
            await message.reply_text("Голосовое слишком длинное. Для MVP обрабатываю до 3 минут.")
            return
        log_event(
            LOGGER,
            level=logging.INFO,
            component="telegram.voice_handler",
            event="voice_message_received",
            message="Voice message received from owner",
            context={
                "user_id": message.from_user.id if message.from_user else None,
                "chat_id": chat.id,
                "message_id": message.message_id,
                "voice_file_id": file_id,
                "voice_duration_sec": duration_seconds,
                "source_kind": source_kind,
            },
        )

        try:
            await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
            transcript = await self._transcribe_voice_message(message, context, file_id=file_id, source_kind=source_kind)
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="telegram.voice_handler",
                event="voice_transcription_failed",
                message="Failed to transcribe voice message",
                context={
                    "chat_id": chat.id,
                    "message_id": message.message_id,
                    "voice_file_id": file_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "source_kind": source_kind,
                },
                exc_info=exc,
            )
            await message.reply_text(self._map_runtime_error_to_user_message(exc))
            return

        log_event(
            LOGGER,
            level=logging.INFO,
            component="telegram.voice_handler",
            event="voice_to_post_generation_started",
            message="Voice transcription completed and handed to post generation",
            context={
                "chat_id": chat.id,
                "message_id": message.message_id,
                "transcript_length": len(transcript),
                "source_kind": source_kind,
            },
        )
        generation_input = f"Расшифровка сообщения ({source_kind}):\n{transcript}"
        await self._generate_and_preview_post(
            message=message,
            chat_id=str(chat.id),
            source_request=f"[{source_kind}] " + transcript,
            generation_input=generation_input,
            post_format="standard",
            post_type_id="educational_short",
            context=context,
        )

    async def _generate_and_preview_post(
        self,
        *,
        message: Message,
        chat_id: str,
        source_request: str,
        generation_input: str,
        post_format: PostFormat,
        post_type_id: str,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        log_event(
            LOGGER,
            level=logging.INFO,
            component="post.draft",
            event="post_generation_started",
            message="Post generation started",
            context={
                "chat_id": chat_id,
                "message_id": message.message_id,
                "input_length": len(generation_input),
                "source_kind": "voice" if source_request.startswith("[voice] ") else "text",
                "post_format": post_format,
                "post_type_id": post_type_id,
            },
        )
        try:
            await context.bot.send_chat_action(chat_id=int(chat_id), action=ChatAction.TYPING)
            pending_post = await self._posting_service.generate_pending_post(
                owner_chat_id=chat_id,
                post_format=post_format,
                post_type_id=post_type_id,
                source_request=source_request,
                generation_input=generation_input,
                source_message_id=message.message_id,
                style_prompt=self._config.style_prompt,
            )
            log_event(
                LOGGER,
                level=logging.INFO,
                component="post.draft",
                event="draft_created",
                message="Draft created",
                context={
                    "post_id": pending_post.id,
                    "chat_id": chat_id,
                    "source": "topic" if source_request.startswith("[topic]") else "message",
                    "post_format": post_format,
                    "post_type_id": post_type_id,
                },
            )
            preview_message = await message.reply_text(
                normalize_markdown_for_telegram(pending_post.post_text),
                disable_web_page_preview=True,
                reply_markup=build_pending_post_keyboard(pending_post.id, self._ui),
            )
            self._posting_service.attach_preview_message(pending_post.id, preview_message.message_id)
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="post.draft",
                event="post_generation_failed",
                message="Failed to generate post",
                context={
                    "chat_id": chat_id,
                    "message_id": message.message_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            await message.reply_text(self._map_runtime_error_to_user_message(exc))

    async def _transcribe_voice_message(
        self,
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        file_id: str,
        source_kind: str,
    ) -> str:
        token = uuid.uuid4().hex[:8]
        stem = f"{message.chat_id}_{message.message_id}_{token}"
        input_path = self._audio_temp_dir / f"{stem}.input"
        wav_path = self._audio_temp_dir / f"{stem}.wav"
        log_event(
            LOGGER,
            level=logging.DEBUG,
            component="telegram.voice_handler",
            event="voice_file_download_started",
            message="Voice file download started",
            context={
                "message_id": message.message_id,
                "voice_file_id": file_id,
                "source_kind": source_kind,
                "download_path": str(input_path),
            },
        )
        try:
            telegram_file = await context.bot.get_file(file_id)
            await telegram_file.download_to_drive(custom_path=str(input_path))
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="telegram.voice_handler",
                event="voice_file_download_failed",
                message="Voice file download failed",
                context={
                    "message_id": message.message_id,
                    "voice_file_id": file_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "source_kind": source_kind,
                },
                exc_info=exc,
            )
            raise
        log_event(
            LOGGER,
            level=logging.DEBUG,
            component="telegram.voice_handler",
            event="voice_file_download_completed",
            message="Voice file download completed",
            context={
                "message_id": message.message_id,
                "voice_file_id": file_id,
                "download_path": str(input_path),
                "source_kind": source_kind,
            },
        )
        try:
            return await self._transcriber.transcribe_telegram_voice(input_path)
        finally:
            with suppress(FileNotFoundError):
                input_path.unlink()
            with suppress(FileNotFoundError):
                wav_path.unlink()

    async def _handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return

        log_event(
            LOGGER,
            level=logging.INFO,
            component="telegram.callback",
            event="callback_received",
            message="Callback query received",
            context={
                "user_id": update.effective_user.id if update.effective_user else None,
                "chat_id": query.message.chat_id if query.message else None,
                "callback_data": query.data,
            },
        )
        if not self._is_allowed_user(update.effective_user.id if update.effective_user else None):
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="telegram.callback",
                event="callback_access_denied",
                message="Callback denied for non-owner user",
                context={
                    "user_id": update.effective_user.id if update.effective_user else None,
                    "chat_id": query.message.chat_id if query.message else None,
                },
            )
            await query.answer()
            return

        namespace, action, raw_id = _parse_callback_data(query.data or "")
        if not action or raw_id is None:
            await query.answer("Неизвестное действие.")
            return

        if namespace == "topic":
            await self._handle_topic_callback(query, action, raw_id, context)
            return

        if namespace == "comment":
            await self._handle_comment_callback(query, action, raw_id)
            return

        if namespace != "post":
            await query.answer("Неизвестное действие.")
            return

        post = self._posting_service.get_pending_post(raw_id)
        if post is None:
            await query.answer("Пост не найден.", show_alert=True)
            return
        log_event(
            LOGGER,
            level=logging.INFO,
            component="telegram.callback",
            event="pending_post_loaded",
            message="Pending post loaded for callback action",
            context={"post_id": raw_id, "action": action},
        )

        try:
            if action == "approve":
                log_event(
                    LOGGER,
                    level=logging.INFO,
                    component="post.approve",
                    event="approve_clicked",
                    message="Approve clicked",
                    context={"post_id": post.id},
                )
                await self._publish_pending_post(query, post)
                return

            if action == "reopen":
                await self._answer_callback(query, "Перегенерирую...")
                log_event(
                    LOGGER,
                    level=logging.INFO,
                    component="post.reopen",
                    event="reopen_clicked",
                    message="Reopen clicked",
                    context={"post_id": post.id},
                )
                await self._reopen_pending_post(query, post, callback_acknowledged=True)
                return

            await query.answer("Неизвестное действие.")
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="telegram.callback",
                event="callback_failed",
                message="Failed to handle callback action",
                context={
                    "post_id": post.id,
                    "action": action,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            await self._answer_callback(query, self._map_runtime_error_to_user_message(exc), show_alert=True)

    async def _handle_topic_callback(self, query, action: str, topic_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        topic = self._topics_service.get_topic(topic_id)
        if topic is None:
            await query.answer("Тема не найдена. Запусти /topic ещё раз.", show_alert=True)
            return

        if action == "pick":
            log_event(
                LOGGER,
                level=logging.INFO,
                component="topic.generator",
                event="topic_selected",
                message="Topic selected",
                context={"topic_id": topic_id, "title": topic.topic_title},
            )
            generation_data = self._topics_service.prepare_post_generation(topic_id)
            if generation_data is None:
                await query.answer("Тема не найдена. Запусти /topic ещё раз.", show_alert=True)
                return
            await query.answer(self._ui.topic_generating)
            if query.message is None:
                await query.answer("Тема не найдена. Запусти /topic ещё раз.", show_alert=True)
                return
            await self._generate_and_preview_post(
                message=query.message,
                chat_id=generation_data.owner_chat_id,
                source_request=generation_data.source_request,
                generation_input=generation_data.generation_input,
                post_format=generation_data.post_format,
                post_type_id=generation_data.post_type_id,
                context=context,
            )
            return

        await query.answer("Неизвестное действие.")

    async def _handle_comment_callback(self, query, action: str, comment_id: int) -> None:
        record = self._comments_service.get_comment(comment_id)
        if record is None:
            await query.answer("Комментарий не найден.", show_alert=True)
            return

        if action in {"generate", "regenerate"}:
            await self._generate_comment_reply(query, comment_id)
            return

        if action == "send":
            await self._send_comment_reply(query, comment_id)
            return

        await query.answer("Неизвестное действие.")

    async def _generate_comment_reply(self, query, comment_id: int) -> None:
        running = self._comment_generation_tasks.get(comment_id)
        if running is not None and not running.done():
            log_event(
                LOGGER,
                level=logging.INFO,
                component="comments.flow",
                event="comment_reply_generation_skipped_already_running",
                message="Comment reply generation skipped because a job is already running",
                context={"comment_id": comment_id},
            )
            await query.answer("Генерация уже запущена.", show_alert=True)
            return

        record = self._comments_service.get_comment(comment_id)
        if record is None:
            await query.answer("Комментарий не найден.", show_alert=True)
            return
        if record.status == "sending":
            await query.answer("Тикет сейчас отправляется, дождитесь завершения.", show_alert=True)
            return
        if record.status == "sent":
            await query.answer("Тикет уже отправлен.", show_alert=True)
            return

        if query.message is not None:
            await query.message.edit_text(
                self._comments_service.render_comment_generating_message(record),
                reply_markup=build_comment_reply_keyboard(comment_id),
                disable_web_page_preview=True,
            )
        log_event(
            LOGGER,
            level=logging.INFO,
            component="comments.flow",
            event="comment_reply_generation_started",
            message="Comment reply generation started",
            context={"comment_id": comment_id},
        )
        await query.answer("Запустил генерацию")

        review_chat_id = query.message.chat_id if query.message is not None else None
        review_message_id = query.message.message_id if query.message is not None else None
        task = asyncio.create_task(
            self._run_comment_reply_generation(
                comment_id=comment_id,
                review_chat_id=review_chat_id,
                review_message_id=review_message_id,
            )
        )
        self._comment_generation_tasks[comment_id] = task
        task.add_done_callback(lambda _: self._comment_generation_tasks.pop(comment_id, None))

    async def _run_comment_reply_generation(
        self,
        *,
        comment_id: int,
        review_chat_id: int | None,
        review_message_id: int | None,
    ) -> None:
        try:
            updated = await self._comments_service.generate_comment_reply(comment_id)
            if updated is None:
                return
            reply_text = updated.generated_reply or ""
            log_event(
                LOGGER,
                level=logging.INFO,
                component="comments.flow",
                event="comment_reply_generated",
                message="Comment reply generated",
                context={"comment_id": comment_id, "reply_length": len(reply_text)},
            )
            target_chat_id = review_chat_id if review_chat_id is not None else int(updated.review_chat_id or 0)
            target_message_id = review_message_id if review_message_id is not None else updated.review_message_id
            if target_chat_id and target_message_id:
                try:
                    await self._application.bot.edit_message_text(
                        chat_id=target_chat_id,
                        message_id=target_message_id,
                        text=self._comments_service.render_comment_review_message(updated, reply_text),
                        reply_markup=build_comment_reply_keyboard(comment_id),
                        disable_web_page_preview=True,
                    )
                except TelegramError as exc:
                    log_event(
                        LOGGER,
                        level=logging.WARNING,
                        component="comments.flow",
                        event="comment_reply_review_message_update_failed",
                        message="Reply generated but failed to update review message",
                        context={
                            "comment_id": comment_id,
                            "review_chat_id": target_chat_id,
                            "review_message_id": target_message_id,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        },
                        exc_info=exc,
                    )
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="comments.flow",
                event="comment_reply_generation_failed",
                message="Failed to generate comment reply",
                context={"comment_id": comment_id, "error_message": str(exc)},
                exc_info=exc,
            )
            self._comments_service.mark_comment_status(comment_id, "failed")
            record = self._comments_service.get_comment(comment_id)
            if review_chat_id is not None and review_message_id is not None and record is not None:
                with suppress(TelegramError):
                    await self._application.bot.edit_message_text(
                        chat_id=review_chat_id,
                        message_id=review_message_id,
                        text=self._comments_service.render_comment_review_message(record),
                        reply_markup=build_comment_generate_keyboard(comment_id),
                        disable_web_page_preview=True,
                    )

    async def _send_comment_reply(self, query, comment_id: int) -> None:
        record = self._comments_service.get_comment(comment_id)
        if record is None:
            await query.answer("Комментарий не найден.", show_alert=True)
            return
        if record.status == "sent":
            await query.answer("Ответ уже отправлен.", show_alert=True)
            return
        if record.status == "generating":
            await query.answer("Подождите, ответ ещё генерируется.", show_alert=True)
            return
        if not record.generated_reply:
            await query.answer("Сначала сгенерируйте ответ.", show_alert=True)
            return
        locked = self._comments_service.acquire_comment_send_lock(comment_id)
        if locked is None:
            current = self._comments_service.get_comment(comment_id)
            if current is not None and current.status in {"sending", "sent"}:
                await query.answer("Этот тикет уже отправляется или отправлен.", show_alert=True)
                return
            await query.answer("Нельзя отправить тикет в текущем состоянии.", show_alert=True)
            return
        try:
            if self._config.comment_reply_mode != "bot":
                self._comments_service.mark_comment_status(comment_id, "failed")
                await query.answer("Режим user-client пока не реализован в этом инкременте.", show_alert=True)
                return
            sent = await self._application.bot.send_message(
                chat_id=locked.source_chat_id,
                text=locked.generated_reply or "",
                reply_to_message_id=locked.source_message_id,
                disable_web_page_preview=True,
            )
            updated = self._comments_service.mark_comment_sent(comment_id, sent.message_id)
            if query.message is not None:
                rendered = self._comments_service.render_comment_sent_message(updated or locked)
                await query.message.edit_text(rendered, reply_markup=None)
            log_event(
                LOGGER,
                level=logging.INFO,
                component="comments.flow",
                event="comment_reply_sent",
                message="Comment reply sent to source chat",
                context={"comment_id": record.id, "sent_message_id": sent.message_id},
            )
            await query.answer("Отправлено")
        except Exception as exc:
            self._comments_service.mark_comment_status(comment_id, "failed")
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="comments.flow",
                event="comment_reply_send_failed",
                message="Failed to send comment reply",
                context={"comment_id": record.id, "error_message": str(exc)},
                exc_info=exc,
            )
            await query.answer("Не удалось отправить ответ.", show_alert=True)

    async def _publish_pending_post(self, query, post: PendingPost) -> None:
        if post.status == "published":
            await query.answer("Уже опубликовано.")
            return
        target_chat_id = post.target_chat_id
        post_target_ready, _ = _validate_target_chat_id(post.target_chat_id)
        if not post_target_ready and self._publish_ready:
            target_chat_id = self._config.target_chat_id
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="post.approve",
                event="publish_target_overridden_from_config",
                message="Invalid target in pending post replaced by configured target",
                context={"post_id": post.id, "post_target_chat_id": post.target_chat_id, "config_target_chat_id": target_chat_id},
            )
        if not self._publish_ready:
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="post.approve",
                event="publish_precheck_not_ready",
                message="Publish precheck flagged target chat, attempting send anyway",
                context={
                    "post_id": post.id,
                    "target_chat_id": target_chat_id,
                    "reason": self._publish_not_ready_reason,
                },
            )

        try:
            sent = await self._application.bot.send_message(
                chat_id=target_chat_id,
                text=normalize_markdown_for_telegram(post.post_text),
                disable_web_page_preview=True,
            )
        except Forbidden as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="post.approve",
                event="post_publish_forbidden",
                message="Failed to publish: bot has no permission for target chat",
                context={
                    "post_id": post.id,
                    "target_chat_id": target_chat_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            raise RuntimeError("publish_forbidden") from exc
        except BadRequest as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="post.approve",
                event="post_publish_bad_request",
                message="Failed to publish: target chat id or request is invalid",
                context={
                    "post_id": post.id,
                    "target_chat_id": target_chat_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            raise RuntimeError("publish_bad_request") from exc
        except TelegramError as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="post.approve",
                event="post_publish_telegram_error",
                message="Failed to publish due to Telegram API error",
                context={
                    "post_id": post.id,
                    "target_chat_id": target_chat_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            raise RuntimeError("publish_telegram_error") from exc
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="post.approve",
                event="post_publish_failed",
                message="Failed to publish post to target chat",
                context={
                    "post_id": post.id,
                    "target_chat_id": target_chat_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            raise

        updated = self._posting_service.mark_pending_post_published(
            post.id,
            published_chat_id=str(sent.chat.id),
            published_message_id=str(sent.message_id),
        )
        try:
            memory_result = self._topics_service.remember_published_topic(post, sent.message_id)
            if memory_result is not None:
                memory_record, created = memory_result
                log_event(
                    LOGGER,
                    level=logging.INFO,
                    component="topic.memory",
                    event="topic_memory_saved" if created else "topic_memory_duplicate_touched",
                    message="Published topic saved to memory" if created else "Published topic already in memory, touched",
                    context={
                        "post_id": post.id,
                        "topic_memory_id": memory_record.id,
                        "topic_hash": memory_record.topic_hash,
                    },
                )
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="topic.memory",
                event="topic_memory_save_failed",
                message="Failed to save published topic into memory",
                context={
                    "post_id": post.id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )

        post_url = self._build_message_url(sent.chat.username, sent.chat.id, sent.message_id)
        if updated is not None and updated.preview_message_id is not None:
            try:
                await self._application.bot.edit_message_reply_markup(
                    chat_id=updated.owner_chat_id,
                    message_id=updated.preview_message_id,
                    reply_markup=build_post_link_keyboard(post_url, self._ui) if post_url else None,
                )
            except TelegramError as exc:
                log_event(
                    LOGGER,
                    level=logging.WARNING,
                    component="post.approve",
                    event="preview_markup_update_failed",
                    message="Post was published but failed to update preview keyboard",
                    context={
                        "post_id": post.id,
                        "owner_chat_id": updated.owner_chat_id,
                        "preview_message_id": updated.preview_message_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                    exc_info=exc,
                )

        log_event(
            LOGGER,
            level=logging.INFO,
            component="post.approve",
            event="draft_approved",
            message="Draft approved and published",
            context={
                "post_id": post.id,
                "target_chat_id": target_chat_id,
                "published_chat_id": sent.chat.id,
                "published_message_id": sent.message_id,
                "post_url_available": bool(post_url),
            },
        )
        await self._answer_callback(query, self._ui.approve_success)

    async def _reopen_pending_post(self, query, post: PendingPost, *, callback_acknowledged: bool = False) -> None:
        updated = await self._posting_service.reopen_pending_post(post.id, style_prompt=self._config.style_prompt)
        if updated is None:
            await self._answer_callback(query, "Пост не найден.", show_alert=True)
            return
        if query.message is not None:
            await query.message.edit_text(
                normalize_markdown_for_telegram(updated.post_text),
                reply_markup=build_pending_post_keyboard(updated.id, self._ui),
                disable_web_page_preview=True,
            )
        log_event(
            LOGGER,
            level=logging.INFO,
            component="post.reopen",
            event="draft_reopened",
            message="Draft reopened with regenerated text",
            context={
                "post_id": updated.id,
                "chat_id": updated.owner_chat_id,
            },
        )
        if not callback_acknowledged:
            await self._answer_callback(query, "Новый вариант готов")

    async def _answer_callback(self, query, text: str | None = None, *, show_alert: bool = False) -> None:
        try:
            if text is None:
                await query.answer()
            else:
                await query.answer(text, show_alert=show_alert)
        except BadRequest as exc:
            lowered = str(exc).lower()
            if "query is too old" in lowered or "query id is invalid" in lowered:
                log_event(
                    LOGGER,
                    level=logging.WARNING,
                    component="telegram.callback",
                    event="callback_answer_skipped_expired",
                    message="Callback answer skipped because query expired",
                    context={
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                return
            raise

    async def _handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error = context.error
        if error and "terminated by other getUpdates request" in str(error):
            log_event(
                LOGGER,
                level=logging.ERROR,
                component="bot.polling",
                event="duplicate_instance",
                message="Another bot instance is already running. Stop duplicate process or disable webhook.",
            )
            return
        log_event(
            LOGGER,
            level=logging.ERROR,
            component="telegram.error",
            event="telegram_unhandled_error",
            message="Unhandled telegram error",
            context={
                "error_type": type(error).__name__ if error else None,
                "error_message": str(error) if error else None,
            },
            exc_info=error,
        )
        del update

    async def _post_init(self, application) -> None:
        await application.bot.set_my_commands(
            [BotCommand(command, description) for command, description in bot_command_entries()]
        )

    async def _reply_text(self, update: Update, text: str) -> None:
        message = self._reply_target(update)
        if message is not None:
            await message.reply_text(text, disable_web_page_preview=True)

    def _reply_target(self, update: Update) -> Message | None:
        if update.effective_message is not None:
            return update.effective_message
        if update.callback_query and update.callback_query.message:
            return update.callback_query.message
        return None

    async def _is_chat_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            admins = await self._application.bot.get_chat_administrators(chat_id=chat_id)
        except TelegramError as exc:
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="comments.flow",
                event="chat_admins_lookup_failed",
                message="Failed to load chat administrators, skipping admin filter",
                context={"chat_id": chat_id, "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )
            return False
        return any(admin.user.id == user_id for admin in admins)

    def _is_filtered_chat_post(self, message: Message) -> bool:
        if message.sender_chat is not None:
            return True
        if message.is_automatic_forward:
            return True
        origin = message.forward_origin
        if origin is None:
            return False
        sender_chat = getattr(origin, "sender_chat", None)
        if sender_chat is None:
            return False
        target_chat = str(self._config.target_chat_id)
        sender_chat_id = str(getattr(sender_chat, "id", ""))
        sender_chat_username = f"@{getattr(sender_chat, 'username', '')}" if getattr(sender_chat, "username", None) else None
        return sender_chat_id == target_chat or (sender_chat_username is not None and sender_chat_username == target_chat)

    def _run_webhook(self) -> None:
        app = web.Application()
        app["telegram_application"] = self._application
        app.router.add_get("/health", self._handle_health)
        app.router.add_post(self._config.telegram_webhook_path, self._handle_webhook_update)
        app.on_startup.append(self._on_webhook_startup)
        app.on_shutdown.append(self._on_webhook_shutdown)

        LOGGER.info("Starting Posting Assistant bot in webhook mode on port %s", self._config.port)
        web.run_app(app, host="0.0.0.0", port=self._config.port)

    async def _on_webhook_startup(self, app: web.Application) -> None:
        del app
        webhook_base = (self._config.telegram_webhook_base_url or "").rstrip("/")
        webhook_url = f"{webhook_base}{self._config.telegram_webhook_path}"
        await self._application.initialize()
        await self._post_init(self._application)
        await self._application.start()
        await self._application.bot.set_webhook(webhook_url)
        log_event(
            LOGGER,
            level=logging.INFO,
            component="bot.webhook",
            event="bot_started",
            message="Registered Telegram webhook",
            context={"webhook_url": webhook_url},
        )

    async def _on_webhook_shutdown(self, app: web.Application) -> None:
        del app
        try:
            await self._application.bot.delete_webhook()
        finally:
            await self._application.stop()
            await self._application.shutdown()
            self._cancel_background_tasks()
            self._cleanup_audio_temp_dir()
            self._db.close()

    async def _handle_health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response({"ok": True, "mode": "webhook"})

    async def _handle_webhook_update(self, request: web.Request) -> web.Response:
        data = await request.json()
        update = Update.de_json(data, self._application.bot)
        await self._application.update_queue.put(update)
        return web.json_response({"ok": True})

    def _is_allowed_user(self, user_id: int | None) -> bool:
        if user_id is None:
            log_event(
                LOGGER,
                level=logging.DEBUG,
                component="auth.owner_access",
                event="owner_access_denied",
                message="Access denied for update without user id",
            )
            return False
        allowed = str(user_id) == str(self._config.owner_telegram_id)
        log_event(
            LOGGER,
            level=logging.INFO if allowed else logging.WARNING,
            component="auth.owner_access",
            event="owner_access_granted" if allowed else "owner_access_denied",
            message="Owner access check completed",
            context={"user_id": user_id},
        )
        return allowed

    def _log_command(self, update: Update, command: str) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        log_event(
            LOGGER,
            level=logging.INFO,
            component="telegram.command",
            event="command_received",
            message="Telegram command received",
            context={
                "command": command,
                "user_id": user.id if user else None,
                "chat_id": chat.id if chat else None,
                "message_id": message.message_id if message else None,
            },
        )

    def _render_queue_item(self, post: PendingPost) -> str:
        preview = post.post_text
        if len(preview) > 700:
            preview = f"{preview[:700].rstrip()}\n\n...[truncated]"

        return "\n".join(
            [
                f"Post #{post.id}",
                f"Status: {post.status}",
                "",
                preview,
            ]
        )

    def _build_message_url(self, chat_username: str | None, chat_id: int, message_id: int) -> str | None:
        if chat_username:
            return f"https://t.me/{chat_username}/{message_id}"

        chat_id_str = str(chat_id)
        if chat_id_str.startswith("-100"):
            return f"https://t.me/c/{chat_id_str[4:]}/{message_id}"

        return None

    def _prepare_audio_temp_dir(self, configured_dir: str) -> Path:
        project_root = Path.cwd().resolve()
        configured_path = Path(configured_dir).expanduser()
        target_path = (project_root / configured_path).resolve() if not configured_path.is_absolute() else configured_path.resolve()
        if project_root not in target_path.parents and target_path != project_root:
            fallback_path = (project_root / "tmp" / "audio").resolve()
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="audio.temp",
                event="audio_temp_dir_rebased",
                message="Configured voice temp dir is outside project root. Rebased to project temp dir.",
                context={"configured_dir": configured_dir, "resolved_dir": str(target_path), "fallback_dir": str(fallback_path)},
            )
            target_path = fallback_path
        target_path.mkdir(parents=True, exist_ok=True)
        log_event(
            LOGGER,
            level=logging.INFO,
            component="audio.temp",
            event="audio_temp_dir_ready",
            message="Audio temp directory prepared",
            context={"audio_temp_dir": str(target_path)},
        )
        return target_path

    def _cleanup_audio_temp_dir(self) -> None:
        try:
            if not self._audio_temp_dir.exists():
                return
            shutil.rmtree(self._audio_temp_dir)
            log_event(
                LOGGER,
                level=logging.INFO,
                component="audio.temp",
                event="audio_temp_dir_cleaned",
                message="Audio temp directory cleaned on shutdown",
                context={"audio_temp_dir": str(self._audio_temp_dir)},
            )
        except Exception as exc:
            log_event(
                LOGGER,
                level=logging.WARNING,
                component="audio.temp",
                event="audio_temp_dir_cleanup_failed",
                message="Failed to cleanup audio temp directory",
                context={"audio_temp_dir": str(self._audio_temp_dir), "error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=exc,
            )

    def _cancel_background_tasks(self) -> None:
        for task in self._comment_generation_tasks.values():
            task.cancel()
        self._comment_generation_tasks.clear()

    def _map_runtime_error_to_user_message(self, error: Exception) -> str:
        message = str(error)

        if "Local transcription backend is not installed" in message:
            return "Локальная расшифровка не установлена. Нужно установить `mlx-whisper`."

        if "voice_transcription_empty" in message:
            return "Не получилось разобрать голосовое. Попробуй записать чуть чётче или отправь текстом."

        if "ffmpeg_not_found" in message:
            return "Не найден ffmpeg. Установите ffmpeg и перезапустите бота."

        if "voice_not_ready" in message:
            return "Голосовой сценарий сейчас недоступен. Проверьте /status и зависимости (ffmpeg, mlx-whisper)."

        if "publish_forbidden" in message:
            return "Не удалось опубликовать: у бота нет прав в TARGET_CHAT_ID."

        if "publish_bad_request" in message:
            return "Не удалось опубликовать: проверьте корректность TARGET_CHAT_ID."

        if "publish_not_ready" in message:
            return "Публикация недоступна: TARGET_CHAT_ID имеет неверный формат. Проверьте /status."

        if "publish_telegram_error" in message:
            return "Не удалось опубликовать: ошибка Telegram API. Проверьте доступ бота к целевому чату."

        if "voice_conversion_failed" in message:
            return "Не удалось подготовить аудио для расшифровки."

        if "voice_model_load_failed" in message:
            return "Не удалось загрузить локальную модель расшифровки."

        if "Voice transcription is disabled" in message:
            return "Транскрибация голосовых отключена в конфигурации."

        if "No Metal device available" in message:
            return "Локальная расшифровка недоступна в текущем окружении. Нужен обычный запуск на macOS с доступом к Metal."

        if "401" in message:
            return "Claude Code не прошёл авторизацию."

        if "Not logged in" in message or "Claude Code is not logged in" in message:
            return "Claude Code не залогинен. Запустите `claude` в терминале и войдите."

        if "subscription" in message or "billing" in message:
            return "Claude Code отклонил запрос из-за подписки или лимитов."

        if "error_max_turns" in message or "Reached maximum number of turns" in message:
            return "Claude не уложился в лимит внутренних шагов. Повторите запрос чуть короче или ещё раз."

        if "too long for a single Telegram message" in message:
            return "Пост получился слишком длинным для одного сообщения Telegram. Попробуйте более узкий запрос."

        if "interview_post_format_invalid" in message:
            return "Не удалось собрать корректный формат 'Вопрос с собеседования'. Попробуйте ещё раз."

        if "invalid_request_error" in message:
            return message or "Claude Code отклонил запрос."

        return "Не удалось обработать сообщение. Проверьте конфигурацию и логи сервиса."

    def _is_comment_source_chat(self, chat_id: int) -> bool:
        configured = self._config.comment_source_chat_id
        if not configured:
            return False
        return str(chat_id) == str(configured)


def _parse_callback_data(callback_data: str) -> tuple[str, str, int | None]:
    parts = callback_data.split(":")
    if len(parts) != 3:
        return "", "", None

    namespace, action, raw_id = parts
    try:
        return namespace, action, int(raw_id)
    except ValueError:
        return namespace, action, None


def _validate_target_chat_id(value: str) -> tuple[bool, str | None]:
    normalized = value.strip()
    if not normalized:
        return False, "empty_target_chat_id"
    if normalized.startswith("@"):
        if len(normalized) < 2:
            return False, "invalid_username_target"
        return True, None
    if re.fullmatch(r"-?\d{5,}", normalized):
        return True, None
    return False, "unsupported_target_chat_id_format"


def main() -> None:
    config = load_config()
    setup_logging(
        level=config.log_level,
        log_format=config.log_format,
        log_to_file=config.log_to_file,
        log_file_path=config.log_file_path,
    )
    log_event(
        LOGGER,
        level=logging.INFO,
        component="bot.runtime",
        event="bot_config_loaded",
        message="Bot configuration loaded",
        context={
            "mode": config.bot_mode,
            "target_chat_id": config.target_chat_id,
            "target_chat_id_source": config.target_chat_id_source,
            "owner_telegram_id": config.owner_telegram_id,
            "topic_generation_model": config.topic_generation_model,
            "transcription_model": config.local_transcribe_model,
            "transcription_enabled": config.voice_transcription_enabled,
            "transcription_provider": config.voice_transcription_provider,
            "voice_max_duration_seconds": config.voice_max_duration_seconds,
            "voice_tmp_dir": config.voice_tmp_dir,
            "obsidian_enabled": bool(config.obsidian_vault_path),
            "role_prompt_path": config.role_prompt_path,
            "style_prompt_path": config.style_prompt_path,
            "themes_prompt_path": config.themes_prompt_path,
            "post_types_config_path": config.post_types_config_path,
            "active_post_types": [item.id for item in config.post_types],
            "topic_layout": {
                "topic_count": config.topic_layout.topic_count,
                "buttons_per_row": config.topic_layout.buttons_per_row,
                "candidate_count": config.topic_layout.candidate_count,
            },
            "comment_source_chat_id": config.comment_source_chat_id,
            "comment_review_chat_id": config.comment_review_chat_id,
            "comment_reply_mode": config.comment_reply_mode,
            "ui_language": config.ui_language,
            "tags_path": config.tag_catalog_path,
        },
    )
    runtime = PostingAssistantBotRuntime(config)
    runtime.run()


if __name__ == "__main__":
    main()
