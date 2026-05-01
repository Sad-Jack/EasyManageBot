from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Literal

from posting_assistant_bot.application.ports import (
    PendingPost,
    PostFormat,
    SuggestedTopicPort,
    TopicGeneratorPort,
    TopicMemoryItem,
    TopicRepositoryPort,
)
from posting_assistant_bot.config import AppConfig, PostTypeConfig
from posting_assistant_bot.logging_utils import log_event
from posting_assistant_bot.topic_memory import (
    build_history_context,
    build_topic_memory_payload,
    filter_duplicate_topics,
    jaccard_similarity,
    normalize_topic_text,
)
from posting_assistant_bot.ui_text import UITexts

TopicSource = Literal["manual_command", "panel_button", "regenerate"]


@dataclass(frozen=True)
class TopicPostGenerationData:
    owner_chat_id: str
    post_format: PostFormat
    post_type_id: str
    source_request: str
    generation_input: str


class TopicsService:
    def __init__(self, *, topic_generator: TopicGeneratorPort, topics: TopicRepositoryPort, config: AppConfig) -> None:
        self._topic_generator = topic_generator
        self._topics = topics
        self._config = config

    async def suggest_topics(
        self,
        *,
        owner_chat_id: str,
        owner_id: str,
        source: TopicSource,
        post_format: PostFormat = "standard",
    ) -> list[SuggestedTopicPort]:
        del post_format
        active_post_types = self._active_post_types()
        topic_count = self._config.topic_layout.topic_count
        candidate_count = self._config.topic_layout.candidate_count
        recent_window = self._config.topic_diversity.recent_window
        similarity_threshold = self._config.topic_diversity.similarity_threshold
        exploration_ratio = self._config.topic_diversity.exploration_ratio

        memory = self._topics.list_recent_topic_memory(limit=recent_window, status="ACTIVE")
        history_context = build_history_context(memory, max_items=recent_window)
        type_hints = tuple(f"{item.label}: {item.prompt_hint}" for item in active_post_types)

        suggested = await self._topic_generator.suggest_topics(
            history_context=history_context,
            post_format="standard",
            desired_count=max(topic_count, candidate_count),
            post_type_hints=type_hints,
        )
        neutral_candidates = self._filter_neutral_topics(list(suggested.topics))
        filtered = filter_duplicate_topics(
            neutral_candidates,
            memory,
            limit=max(topic_count, candidate_count),
            threshold=similarity_threshold,
        )
        diversified = self._apply_diversification(
            candidates=filtered,
            memory=memory,
            topic_count=topic_count,
            exploration_ratio=exploration_ratio,
            similarity_threshold=similarity_threshold,
        )
        while len(diversified) < topic_count:
            diversified.extend(self._fallback_topics(diversified, memory, similarity_threshold))
            diversified = diversified[:topic_count]

        slots = self._build_post_type_slots(active_post_types, topic_count)
        records: list[SuggestedTopicPort] = []
        for index, title in enumerate(diversified):
            post_type = slots[index]
            records.append(
                self._topics.create_suggested_topic(
                    owner_chat_id=owner_chat_id,
                    owner_id=owner_id,
                    topic_title=title,
                    topic_angle="",
                    why_write="",
                    source=source,
                    post_format=self._post_format_for_type(post_type.id),
                    post_type_id=post_type.id,
                )
            )
        return records

    def get_topic(self, topic_id: int) -> SuggestedTopicPort | None:
        topic = self._topics.get_suggested_topic_by_id(topic_id)
        if topic is None:
            return None
        if topic.post_type_id == "myth_vs_fact":
            return None
        return topic

    def attach_topic_message(self, topic_id: int, topic_message_id: int) -> SuggestedTopicPort | None:
        return self._topics.attach_topic_message(topic_id, topic_message_id)

    async def regenerate_topic(self, topic_id: int) -> SuggestedTopicPort | None:
        topic = self._topics.get_suggested_topic_by_id(topic_id)
        if topic is None:
            return None
        if topic.post_type_id == "myth_vs_fact":
            return None

        memory = self._topics.list_recent_topic_memory(limit=self._config.topic_diversity.recent_window, status="ACTIVE")
        hint = self._label_for_post_type(topic.post_type_id)
        suggested = await self._topic_generator.suggest_topics(
            history_context=build_history_context(memory, max_items=self._config.topic_diversity.recent_window),
            post_format=topic.post_format,
            desired_count=4,
            post_type_hints=(hint,),
        )
        candidates = filter_duplicate_topics(
            list(suggested.topics),
            memory,
            limit=4,
            threshold=self._config.topic_diversity.similarity_threshold,
        )
        replacement_title = candidates[0] if candidates else suggested.topics[0]
        return self._topics.update_suggested_topic_content(
            topic_id,
            topic_title=replacement_title,
            topic_angle="",
            why_write="",
            source="regenerate",
            post_format=topic.post_format,
            post_type_id=topic.post_type_id,
        )

    def prepare_post_generation(self, topic_id: int) -> TopicPostGenerationData | None:
        topic = self._topics.get_suggested_topic_by_id(topic_id)
        if topic is None:
            return None
        if topic.post_type_id == "myth_vs_fact":
            return None

        self._topics.mark_suggested_topic_status(topic_id, "post_generated")
        if topic.post_format == "interview_question":
            generation_input = "\n".join(
                [
                    "Формат: Вопрос с собеседования.",
                    f"Тип поста: {topic.post_type_id}",
                    f"Вопрос/тема: {topic.topic_title}",
                ]
            )
        else:
            generation_input = "\n".join(
                [
                    f"Тип поста: {topic.post_type_id}",
                    f"Тема: {topic.topic_title}",
                ]
            )
        return TopicPostGenerationData(
            owner_chat_id=topic.owner_chat_id,
            post_format=topic.post_format,
            post_type_id=topic.post_type_id,
            source_request=f"[topic:{topic.post_type_id}] {topic.topic_title}",
            generation_input=generation_input,
        )

    def mark_topic_deleted(self, topic_id: int) -> SuggestedTopicPort | None:
        return self._topics.mark_suggested_topic_status(topic_id, "deleted")

    def remember_published_topic(self, pending_post: PendingPost, published_message_id: int | None) -> tuple[TopicMemoryItem, bool] | None:
        source_topic = self._extract_topic_from_pending_post(pending_post)
        if not source_topic:
            return None
        payload = build_topic_memory_payload(source_topic)
        return self._topics.create_or_touch_topic_memory(
            source_topic_text=payload.source_topic_text,
            topic_hash=payload.topic_hash,
            topic_summary_compact=payload.topic_summary_compact,
            topic_keywords_compact=payload.topic_keywords_compact,
            semantic_fingerprint=payload.semantic_fingerprint,
            post_id=pending_post.id,
            message_id=published_message_id,
            status="ACTIVE",
        )

    @staticmethod
    def render_topics_message(topics: list[str], ui: UITexts) -> str:
        lines = [ui.topics_header, ""]
        for index, title in enumerate(topics, start=1):
            lines.append(f"{index}. {title}")
        lines.extend(["", ui.topics_pick_prompt])
        return "\n".join(lines)

    def _active_post_types(self) -> tuple[PostTypeConfig, ...]:
        active = tuple(item for item in self._config.post_types if item.enabled and item.id != "myth_vs_fact")
        if active:
            return active
        return (
            PostTypeConfig(
                id="educational_short",
                label="Короткий обучающий пост",
                enabled=True,
                priority=100,
                prompt_hint="Короткая практичная тема для аудитории, изучающей ML/AI.",
            ),
        )

    @staticmethod
    def _build_post_type_slots(post_types: tuple[PostTypeConfig, ...], topic_count: int) -> list[PostTypeConfig]:
        slots: list[PostTypeConfig] = []
        while len(slots) < topic_count:
            slots.extend(post_types)
        return slots[:topic_count]

    @staticmethod
    def _extract_topic_from_pending_post(pending_post: PendingPost) -> str:
        source = (pending_post.source_request or "").strip()
        if source.startswith("[topic:"):
            parts = source.split("] ", 1)
            if len(parts) == 2:
                return parts[1].strip()
        generation_input = (pending_post.generation_input or "").strip()
        for line in generation_input.splitlines():
            line = line.strip()
            if line.lower().startswith("тема:"):
                return line.split(":", 1)[1].strip()
            if line.lower().startswith("вопрос/тема:"):
                return line.split(":", 1)[1].strip()
        return normalize_topic_text(source or generation_input)

    def _label_for_post_type(self, post_type_id: str) -> str:
        for item in self._config.post_types:
            if item.id == post_type_id:
                return f"{item.label}: {item.prompt_hint}"
        return post_type_id

    @staticmethod
    def _post_format_for_type(post_type_id: str) -> PostFormat:
        if post_type_id == "interview_question":
            return "interview_question"
        return "standard"

    def _apply_diversification(
        self,
        *,
        candidates: list[str],
        memory: list[TopicMemoryItem],
        topic_count: int,
        exploration_ratio: float,
        similarity_threshold: float,
    ) -> list[str]:
        if not candidates:
            return []
        scored: list[tuple[float, str]] = []
        for candidate in candidates:
            normalized = normalize_topic_text(candidate)
            max_similarity = 0.0
            for item in memory:
                similarity = jaccard_similarity(normalized, item.topic_summary_compact)
                if similarity > max_similarity:
                    max_similarity = similarity
            scored.append((max_similarity, candidate))
        scored.sort(key=lambda item: item[0])

        explore_count = max(1, int(round(topic_count * exploration_ratio)))
        core_count = max(0, topic_count - explore_count)
        explore = [item[1] for item in scored[:explore_count]]
        core = [item[1] for item in scored if item[0] < similarity_threshold][:core_count]

        selected: list[str] = []
        for title in core + explore:
            if title in selected:
                continue
            selected.append(title)
            if len(selected) >= topic_count:
                break
        return selected

    def _fallback_topics(self, existing: list[str], memory: list[TopicMemoryItem], threshold: float) -> list[str]:
        pool = [
            "Как быстро оценить качество данных до обучения модели",
            "Как не утонуть в фичах и выбрать первые рабочие признаки",
            "Почему baseline должен появляться в проекте в первый день",
            "Как понять, что проблема в данных, а не в алгоритме",
            "Какие проверки сделать перед публикацией ML-результата",
            "Как вести заметки по экспериментам, чтобы не терять прогресс",
            "Что делать, если метрика в offline растёт, а в проде падает",
            "Как объяснить выбор метрики бизнесу без формул",
            "Какие ошибки чаще всего ломают первый ML MVP",
        ]
        candidates = existing + pool
        return filter_duplicate_topics(
            candidates,
            memory,
            limit=self._config.topic_layout.topic_count,
            threshold=threshold,
        )[len(existing) :]

    def _filter_neutral_topics(self, candidates: list[str]) -> list[str]:
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        for raw in candidates:
            candidate = raw.strip()
            if not candidate:
                continue
            rewritten = self._rewrite_personal_to_neutral(candidate)
            reason = self._neutral_style_reject_reason(rewritten)
            if reason:
                rejected.append({"topic": candidate, "reason": reason})
                continue
            accepted.append(rewritten)

        if rejected:
            log_event(
                logging.getLogger(__name__),
                level=logging.INFO,
                component="topic.policy",
                event="generated_topic_rejected_by_style",
                message="Rejected generated topics by neutral style policy",
                context={"rejected_count": len(rejected), "rejected": rejected},
            )
        return accepted

    def _neutral_style_reject_reason(self, topic: str) -> str | None:
        lower = normalize_topic_text(topic)
        for pattern, reason in _PERSONAL_STYLE_PATTERNS:
            if pattern.search(lower):
                return reason
        if not _NEUTRAL_START_PATTERN.search(lower):
            return "missing_neutral_educational_pattern"
        return None

    @staticmethod
    def _rewrite_personal_to_neutral(topic: str) -> str:
        rewritten = topic.strip()
        replacements = (
            (r"(?i)\bпочему я\b", "Почему"),
            (r"(?i)\bгде я\b", "Где"),
            (r"(?i)\bкак выглядит мой\b", "Как выглядит"),
            (r"(?i)\bмой путь\b", "путь развития в ML"),
            (r"(?i)\bна этой неделе\b", ""),
            (r"(?i)\bчто я сделал\b", "что было сделано"),
            (r"(?i)\bчестный срез\b", "разбор текущего состояния"),
            (r"(?i)\bя перестал\b", "почему отказ от"),
            (r"(?i)\bмой\b", ""),
            (r"(?i)\bмы\b", ""),
            (r"(?i)\bя\b", ""),
        )
        for pattern, replacement in replacements:
            rewritten = re.sub(pattern, replacement, rewritten)
        rewritten = re.sub(r"\s+", " ", rewritten).strip(" .,-")
        if rewritten:
            rewritten = rewritten[0].upper() + rewritten[1:]
        return rewritten


_PERSONAL_STYLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|\s)я($|\s)"), "contains_first_person_ya"),
    (re.compile(r"\bмой\b|\bмоя\b|\bмоё\b|\bмои\b"), "contains_possessive_moi"),
    (re.compile(r"(^|\s)мы($|\s)"), "contains_first_person_we"),
    (re.compile(r"\bна этой неделе\b"), "contains_time_diary_marker"),
    (re.compile(r"\bмой путь\b"), "contains_personal_journey"),
    (re.compile(r"\bчестный срез\b"), "contains_diary_marker"),
    (re.compile(r"\bчто я сделал\b"), "contains_personal_action_marker"),
)

_NEUTRAL_START_PATTERN = re.compile(
    r"^(что|чем|как|почему|когда|зачем|где|какие|какой|можно ли|стоит ли)\b",
    re.IGNORECASE,
)
