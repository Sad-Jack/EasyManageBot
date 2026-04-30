from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import time
from typing import Any

from posting_assistant_bot.application.ports import PostFormat
from posting_assistant_bot.claude.client import ClaudeCodeClient, ClaudeCodeClientOptions
from posting_assistant_bot.config import AppConfig
from posting_assistant_bot.logging_utils import log_event
from posting_assistant_bot.obsidian.vault import ObsidianVault

POST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "post_text": {"type": "string"},
    },
    "required": ["post_text"],
}

TOPIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
        }
    },
    "required": ["topics"],
}

COMMENT_REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply_text": {"type": "string"},
    },
    "required": ["reply_text"],
}


@dataclass(frozen=True)
class GeneratedPost:
    post_text: str


@dataclass(frozen=True)
class SuggestedTopics:
    topics: tuple[str, str, str, str]


class PostingAssistantOrchestrator:
    def __init__(self, config: AppConfig, vault: ObsidianVault) -> None:
        self._config = config
        self._vault = vault
        self._client = ClaudeCodeClient(
            ClaudeCodeClientOptions(
                cwd=str(Path.cwd()),
                model=config.claude_code_model,
                add_dirs=[config.obsidian_vault_path] if config.obsidian_vault_path else [],
            )
        )

    async def generate_post(
        self,
        *,
        user_message: str,
        style_prompt: str | None = None,
        post_format: PostFormat = "standard",
    ) -> GeneratedPost:
        obsidian_context = await self._load_obsidian_context(user_message)
        post_text = ""
        validation_error: str | None = None
        max_attempts = 3 if post_format == "interview_question" else 1
        for _ in range(max_attempts):
            post_text = await self._generate_once(
                user_message=user_message,
                obsidian_context=obsidian_context,
                style_prompt=style_prompt or self._config.style_prompt,
                post_format=post_format,
                validation_error=validation_error,
            )
            if post_format != "interview_question":
                break
            ok, validation_error = _validate_interview_post_structure(post_text)
            if ok:
                break

        if len(post_text) > 3900:
            post_text = await self._shorten_post(post_text)

        if len(post_text) > 3900:
            raise RuntimeError("Generated post is too long for a single Telegram message.")
        if post_format == "interview_question":
            valid, _ = _validate_interview_post_structure(post_text)
            if not valid:
                raise RuntimeError("interview_post_format_invalid")

        return GeneratedPost(post_text=post_text)

    async def suggest_topics(
        self,
        *,
        history_context: str | None = None,
        post_format: PostFormat = "standard",
        desired_count: int = 4,
        post_type_hints: tuple[str, ...] | None = None,
    ) -> SuggestedTopics:
        started_at = time.perf_counter()
        log_event(
            logging.getLogger(__name__),
            level=logging.INFO,
            component="claude.topic",
            event="topic_generation_started",
            message="Claude topic generation started",
            context={"model": self._config.claude_code_model},
        )
        if post_format == "interview_question":
            format_block = "\n".join(
                [
                    "Формат списка: вопросы для ML-собеседования.",
                    "Каждый пункт должен быть формулировкой вопроса в одну строку.",
                    "В конце каждого вопроса ставь вопросительный знак.",
                    "",
                    "Опорный пул тем (не копируй дословно, вариируй формулировки):",
                    "\n".join(f"- {item}" for item in _INTERVIEW_TOPIC_POOL),
                ]
            )
        else:
            format_block = "Формат списка: обычные темы для образовательных Telegram-постов."
        post_types_block = ""
        if post_type_hints:
            post_types_block = "\n".join(
                [
                    "",
                    "Активные типы постов. Покрой темы равномерно по этим типам:",
                    *(f"- {hint}" for hint in post_type_hints),
                ]
            )
        history_block = (
            f"\n\nИстория уже опубликованных тем (избегай повторов и очень близких формулировок):\n{history_context}"
            if history_context
            else "\n\nИстория тем пока пустая."
        )
        try:
            raw = await self._client.prompt_structured(
                system_prompt=self._config.role_prompt,
                prompt=(
                    f"Предложи ровно {desired_count} тем для следующего поста Posting Assistant. "
                    "Темы должны быть короткими, практичными и в стиле Telegram.\n"
                    f"Ограничитель тем:\n{self._config.themes_prompt}\n\n"
                    f"{format_block}"
                    f"{post_types_block}"
                    f"{history_block}"
                ),
                schema=TOPIC_SCHEMA,
                max_turns=3,
            )
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="claude.topic",
                event="topic_generation_failed",
                message="Claude topic generation failed",
                context={
                    "model": self._config.claude_code_model,
                    "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            raise

        raw_topics = raw.get("topics", [])
        topics = [str(item).strip() for item in raw_topics if str(item).strip()]
        if len(topics) != desired_count:
            raise ValueError(f"Claude must return exactly {desired_count} topics.")

        log_event(
            logging.getLogger(__name__),
            level=logging.INFO,
            component="claude.topic",
            event="topic_generation_completed",
            message="Claude topic generation completed",
            context={
                "model": self._config.claude_code_model,
                "topics_count": len(topics),
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        return SuggestedTopics(topics=tuple(topics))

    async def generate_comment_reply(self, *, comment_text: str) -> str:
        started_at = time.perf_counter()
        log_event(
            logging.getLogger(__name__),
            level=logging.INFO,
            component="claude.comment_reply",
            event="comment_reply_generation_started",
            message="Claude comment reply generation started",
            context={"model": self._config.claude_code_model, "comment_length": len(comment_text)},
        )
        try:
            raw = await self._client.prompt_structured(
                system_prompt=self._config.role_prompt,
                prompt="\n".join(
                    [
                        "Сформируй короткий, полезный и спокойный ответ на комментарий пользователя.",
                        "Стиль ответа:",
                        self._config.style_prompt,
                        "",
                        "Ограничитель тем:",
                        self._config.themes_prompt,
                        "",
                        f"Комментарий пользователя:\n{comment_text}",
                    ]
                ),
                schema=COMMENT_REPLY_SCHEMA,
                max_turns=3,
            )
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="claude.comment_reply",
                event="comment_reply_generation_failed",
                message="Claude comment reply generation failed",
                context={
                    "model": self._config.claude_code_model,
                    "comment_length": len(comment_text),
                    "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            raise

        reply_text = str(raw.get("reply_text", "")).strip()
        if not reply_text:
            raise ValueError("Claude returned an empty reply_text")
        log_event(
            logging.getLogger(__name__),
            level=logging.INFO,
            component="claude.comment_reply",
            event="comment_reply_generation_completed",
            message="Claude comment reply generation completed",
            context={
                "model": self._config.claude_code_model,
                "reply_length": len(reply_text),
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        return reply_text

    async def _generate_once(
        self,
        *,
        user_message: str,
        obsidian_context: str,
        style_prompt: str,
        post_format: PostFormat,
        validation_error: str | None,
    ) -> str:
        started_at = time.perf_counter()
        log_event(
            logging.getLogger(__name__),
            level=logging.INFO,
            component="claude.generation",
            event="claude_generation_started",
            message="Claude generation started",
            context={
                "model": self._config.claude_code_model,
                "input_length": len(user_message),
                "obsidian_context_length": len(obsidian_context),
            },
        )
        allowed_tags_block = "\n".join(f"- {tag}" for tag in self._config.tag_catalog)
        prompt_lines = [
            "Ниже сырой вход автора.",
            "Сделай из него готовый пост для Telegram-канала Posting Assistant.",
            "Верни только итоговый пост.",
            "Учитывай стиль из блока ниже, но не меняй роль ассистента.",
            "Соблюдай формат поста из отдельного блока.",
            "",
            "Стиль генерации:",
            style_prompt or self._config.style_prompt,
            "",
            "Формат поста:",
            _post_format_instructions(post_format),
            "",
            "Ограничитель тем:",
            self._config.themes_prompt,
        ]
        if allowed_tags_block:
            prompt_lines.extend(
                [
                    "",
                    "Используй только теги из разрешённого списка ниже.",
                    "",
                    "Разрешённые теги:",
                    allowed_tags_block,
                ]
            )
        prompt_lines.extend(
            [
                "",
                "Важно: не добавляй markdown-маркеры вроде ** и __.",
                "",
                "Сырой вход:",
                user_message,
                "",
                "Дополнительный локальный контекст:",
                obsidian_context if obsidian_context else "Нет",
            ]
        )
        if validation_error:
            prompt_lines.extend(
                [
                    "",
                    "Предыдущая попытка была отклонена проверкой формата. Исправь структуру строго.",
                    f"Причина: {validation_error}",
                ]
            )
        try:
            raw = await self._client.prompt_structured(
                system_prompt=self._config.role_prompt,
                prompt="\n".join(prompt_lines),
                schema=POST_SCHEMA,
                max_turns=4,
            )
        except Exception as exc:
            log_event(
                logging.getLogger(__name__),
                level=logging.ERROR,
                component="claude.generation",
                event="claude_generation_failed",
                message="Claude generation failed",
                context={
                    "model": self._config.claude_code_model,
                    "input_length": len(user_message),
                    "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                exc_info=exc,
            )
            raise

        post_text = str(raw.get("post_text", "")).strip()
        if not post_text:
            raise ValueError("Claude returned an empty post_text")
        log_event(
            logging.getLogger(__name__),
            level=logging.INFO,
            component="claude.generation",
            event="claude_generation_completed",
            message="Claude generation completed",
            context={
                "model": self._config.claude_code_model,
                "output_length": len(post_text),
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        return post_text

    async def _shorten_post(self, post_text: str) -> str:
        allowed_tags_block = "\n".join(f"- {tag}" for tag in self._config.tag_catalog)
        prompt_lines = [
            "Сожми следующий пост так, чтобы он точно поместился в одно сообщение Telegram.",
            "Сохрани смысл и общий тон.",
            "Цель: не больше 3500 символов.",
        ]
        if allowed_tags_block:
            prompt_lines.extend(
                [
                    "Не добавляй новые теги вне разрешённого списка.",
                    "",
                    "Разрешённые теги:",
                    allowed_tags_block,
                ]
            )
        prompt_lines.extend(["", post_text])
        raw = await self._client.prompt_structured(
            system_prompt=self._config.role_prompt,
            prompt="\n".join(prompt_lines),
            schema=POST_SCHEMA,
            max_turns=3,
        )

        shortened = str(raw.get("post_text", "")).strip()
        if not shortened:
            raise ValueError("Claude returned an empty shortened post_text")
        return shortened

    async def _load_obsidian_context(self, query: str) -> str:
        if not self._vault.is_configured():
            return ""

        normalized = query.strip()
        if not normalized:
            return ""

        found = await self._vault.search(normalized, 3)
        if not found:
            return ""

        return "\n".join(f"- {item.path}: {item.snippet}" for item in found)


_INTERVIEW_TOPIC_POOL: tuple[str, ...] = (
    "Что такое признаки (X) и target (y)?",
    "Чем классификация отличается от регрессии?",
    "Что такое train / validation / test?",
    "Почему нельзя тестировать на train?",
    "Что такое data leakage?",
    "Что такое accuracy и когда она врет?",
    "Precision vs Recall — в чем разница?",
    "Что такое F1-score и зачем он нужен?",
    "Когда важнее precision, а когда recall?",
    "MAE vs MSE vs RMSE — как выбрать?",
    "Что такое overfitting и underfitting?",
    "Как понять, что модель переобучилась?",
    "Как бороться с переобучением?",
    "Что такое bias и variance (на пальцах)?",
    "Зачем нужна baseline модель?",
)


def _post_format_instructions(post_format: PostFormat) -> str:
    if post_format != "interview_question":
        return "Обычный образовательный пост для Telegram."
    return "\n".join(
        [
            "Формат: Вопрос с собеседования.",
            "Структура обязательна:",
            "1) Первая строка: Вопрос с собеседования",
            "2) Вторая строка: Вопрос: ...?",
            "3) Далее блок: Объяснение: ... (кратко и простым языком)",
            "4) Блок Пример: ... добавляй только когда он уместен.",
            "Без лишней теории, без воды, для уровня junior/middle.",
        ]
    )


def _validate_interview_post_structure(post_text: str) -> tuple[bool, str | None]:
    normalized = post_text.strip()
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if len(lines) < 3:
        return False, "слишком короткий текст для interview-формата"
    if lines[0].lower() != "вопрос с собеседования":
        return False, "первая строка должна быть 'Вопрос с собеседования'"
    if not lines[1].lower().startswith("вопрос:"):
        return False, "вторая строка должна начинаться с 'Вопрос:'"
    question_line = lines[1]
    if "?" not in question_line:
        return False, "строка вопроса должна содержать '?'"
    if re.search(r"^\s*объяснение\s*:", normalized, re.IGNORECASE | re.MULTILINE) is None:
        return False, "отсутствует блок 'Объяснение:'"
    return True, None
