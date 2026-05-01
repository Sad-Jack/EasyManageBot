from __future__ import annotations

import os
import logging
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_ROLE_PROMPT_PATH = PACKAGE_ROOT / "prompts" / "role_prompt.txt"
DEFAULT_STYLE_PROMPT_PATH = PACKAGE_ROOT / "prompts" / "style_prompt.txt"
DEFAULT_THEMES_PROMPT_PATH = PACKAGE_ROOT / "prompts" / "themes_prompt.txt"
DEFAULT_TAGS_PATH = PACKAGE_ROOT / "prompts" / "tags.txt"
DEFAULT_POST_TYPES_CONFIG_PATH = PACKAGE_ROOT / "prompts" / "post_types.json"
DEFAULT_POST_STYLE_PROMPT = (
    "Пиши живо и понятно для Telegram. Без канцелярита, с короткими абзацами и практическими выводами."
)
DEFAULT_ROLE_PROMPT = "Ты — Posting Assistant Bot."
DEFAULT_THEMES_PROMPT = "themes: base ml"


@dataclass(frozen=True)
class PostTypeConfig:
    id: str
    label: str
    enabled: bool
    priority: int
    prompt_hint: str


@dataclass(frozen=True)
class TopicLayoutConfig:
    buttons_per_row: int
    topic_count: int
    candidate_count: int


@dataclass(frozen=True)
class TopicDiversityConfig:
    similarity_threshold: float
    exploration_ratio: float
    recent_window: int


@dataclass(frozen=True)
class AppConfig:
    node_env: str
    telegram_bot_token: str
    claude_code_model: str
    topic_generation_model: str
    topic_generation_max_turns: int
    topic_generation_retry_limit: int
    bot_mode: str
    telegram_webhook_base_url: str | None
    telegram_webhook_path: str
    port: int
    owner_telegram_id: str
    target_chat_id: str
    target_chat_id_source: str
    obsidian_vault_path: str | None
    sqlite_path: str
    role_prompt: str
    role_prompt_path: str
    style_prompt: str
    style_prompt_path: str
    themes_prompt: str
    themes_prompt_path: str
    post_types_config_path: str
    post_types: tuple[PostTypeConfig, ...]
    topic_layout: TopicLayoutConfig
    topic_diversity: TopicDiversityConfig
    tag_catalog: tuple[str, ...]
    tag_catalog_path: str
    voice_transcription_enabled: bool
    voice_transcription_provider: str
    local_transcribe_model: str
    voice_max_duration_seconds: int
    voice_tmp_dir: str
    comment_source_chat_id: str | None
    comment_review_chat_id: str | None
    comment_reply_mode: str
    ui_language: str
    log_level: str
    log_format: str
    log_to_file: bool
    log_file_path: str


def load_config() -> AppConfig:
    node_env = _get_env("NODE_ENV", "development")
    if node_env not in {"development", "production", "test"}:
        raise ValueError("NODE_ENV must be one of: development, production, test")

    telegram_bot_token = _get_required_env("TELEGRAM_BOT_TOKEN")
    claude_code_model = _get_first_env(("ANTHROPIC_MODEL", "CLAUDE_CODE_MODEL"), "sonnet")
    topic_generation_model = _get_env("TOPIC_GENERATION_MODEL", "haiku")
    topic_generation_max_turns = _get_positive_int("TOPIC_GENERATION_MAX_TURNS", 4)
    topic_generation_retry_limit = _get_positive_int("TOPIC_GENERATION_RETRY_LIMIT", 1)

    bot_mode = _get_env("BOT_MODE", "polling")
    if bot_mode not in {"polling", "webhook"}:
        raise ValueError("BOT_MODE must be either 'polling' or 'webhook'")

    telegram_webhook_base_url = _get_optional_env("TELEGRAM_WEBHOOK_BASE_URL")
    if telegram_webhook_base_url and not _looks_like_url(telegram_webhook_base_url):
        raise ValueError("TELEGRAM_WEBHOOK_BASE_URL must be a valid URL")

    telegram_webhook_path = _normalize_webhook_path(_get_env("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook"))
    port = _get_positive_int("PORT", 3000)

    if bot_mode == "webhook" and not telegram_webhook_base_url:
        raise ValueError("TELEGRAM_WEBHOOK_BASE_URL is required when BOT_MODE=webhook")

    owner_telegram_id = _get_first_required_env(("OWNER_TELEGRAM_ID", "BOT_OWNER_TELEGRAM_ID"))
    target_chat_id_raw = _get_required_env("TARGET_CHAT_ID")
    target_chat_id_source = "TARGET_CHAT_ID"
    target_chat_id = _normalize_target_chat_id(target_chat_id_raw)
    role_prompt_path = _get_first_env(
        ("ROLE_PROMPT_PATH",),
        str(DEFAULT_ROLE_PROMPT_PATH),
    )
    style_prompt_path = _get_env("STYLE_PROMPT_PATH", str(DEFAULT_STYLE_PROMPT_PATH))
    themes_prompt_path = _get_env("THEMES_PROMPT_PATH", str(DEFAULT_THEMES_PROMPT_PATH))
    post_types_config_path = _get_env("POST_TYPES_CONFIG_PATH", str(DEFAULT_POST_TYPES_CONFIG_PATH))
    tag_catalog_path = _get_first_env(("TAGS_PATH",), str(DEFAULT_TAGS_PATH))
    role_prompt = _load_text_file_with_fallback(
        role_prompt_path,
        fallback=DEFAULT_ROLE_PROMPT,
        warning_key="role_prompt",
    )
    style_prompt = _load_text_file_with_fallback(
        style_prompt_path,
        fallback=_get_env("DEFAULT_POST_STYLE_PROMPT", DEFAULT_POST_STYLE_PROMPT),
        warning_key="style_prompt",
    )
    themes_prompt = _load_text_file_with_fallback(
        themes_prompt_path,
        fallback=DEFAULT_THEMES_PROMPT,
        warning_key="themes_prompt",
    )
    post_types, topic_layout, topic_diversity = _load_post_types_config_with_fallback(post_types_config_path)
    tag_catalog = _load_tag_catalog_with_fallback(
        tag_catalog_path,
        fallback=(),
        warning_key="tags",
    )
    voice_transcription_enabled = _get_bool_env("VOICE_TRANSCRIPTION_ENABLED", True)
    voice_transcription_provider = _get_env("VOICE_TRANSCRIPTION_PROVIDER", "mlx-whisper").lower()
    if voice_transcription_provider not in {"mlx-whisper"}:
        raise ValueError("VOICE_TRANSCRIPTION_PROVIDER must be 'mlx-whisper' for now")
    comment_reply_mode = _get_env("COMMENT_REPLY_MODE", "bot").lower()
    if comment_reply_mode not in {"bot", "user"}:
        raise ValueError("COMMENT_REPLY_MODE must be either 'bot' or 'user'")
    comment_source_chat_id = _get_optional_env("COMMENT_SOURCE_CHAT_ID")
    comment_review_chat_id = _get_optional_env("COMMENT_REVIEW_CHAT_ID")
    if bool(comment_source_chat_id) != bool(comment_review_chat_id):
        raise ValueError("COMMENT_SOURCE_CHAT_ID and COMMENT_REVIEW_CHAT_ID must be set together")
    ui_language = _get_env("UI_LANGUAGE", "ru").lower()
    if ui_language not in {"ru", "en"}:
        raise ValueError("UI_LANGUAGE must be either 'ru' or 'en'")
    log_level = _get_env("LOG_LEVEL", "INFO").upper()
    log_format = _get_env("LOG_FORMAT", "json").lower()
    if log_format not in {"json", "text"}:
        raise ValueError("LOG_FORMAT must be either 'json' or 'text'")

    return AppConfig(
        node_env=node_env,
        telegram_bot_token=telegram_bot_token,
        claude_code_model=claude_code_model,
        topic_generation_model=topic_generation_model,
        topic_generation_max_turns=topic_generation_max_turns,
        topic_generation_retry_limit=topic_generation_retry_limit,
        bot_mode=bot_mode,
        telegram_webhook_base_url=telegram_webhook_base_url,
        telegram_webhook_path=telegram_webhook_path,
        port=port,
        owner_telegram_id=owner_telegram_id,
        target_chat_id=target_chat_id,
        target_chat_id_source=target_chat_id_source,
        obsidian_vault_path=_get_optional_env("OBSIDIAN_VAULT_PATH"),
        sqlite_path=_get_env("SQLITE_PATH", ".data/posting-assistant.sqlite"),
        role_prompt=role_prompt,
        role_prompt_path=role_prompt_path,
        style_prompt=style_prompt,
        style_prompt_path=style_prompt_path,
        themes_prompt=themes_prompt,
        themes_prompt_path=themes_prompt_path,
        post_types_config_path=post_types_config_path,
        post_types=post_types,
        topic_layout=topic_layout,
        topic_diversity=topic_diversity,
        tag_catalog=tag_catalog,
        tag_catalog_path=tag_catalog_path,
        voice_transcription_enabled=voice_transcription_enabled,
        voice_transcription_provider=voice_transcription_provider,
        local_transcribe_model=_get_env("LOCAL_TRANSCRIBE_MODEL", "mlx-community/whisper-small-mlx"),
        voice_max_duration_seconds=_get_positive_int("VOICE_MAX_DURATION_SECONDS", 180),
        voice_tmp_dir=_get_env("VOICE_TMP_DIR", "tmp/audio"),
        comment_source_chat_id=comment_source_chat_id,
        comment_review_chat_id=comment_review_chat_id,
        comment_reply_mode=comment_reply_mode,
        ui_language=ui_language,
        log_level=log_level,
        log_format=log_format,
        log_to_file=_get_bool_env("LOG_TO_FILE", False),
        log_file_path=_get_env("LOG_FILE_PATH", "logs/posting-assistant-bot.log"),
    )


def _get_required_env(name: str) -> str:
    value = _get_optional_env(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _get_first_required_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = _get_optional_env(name)
        if value:
            return value

    joined = ", ".join(names)
    raise ValueError(f"One of these environment variables is required: {joined}")


def _get_first_required_env_with_source(names: tuple[str, ...]) -> tuple[str, str]:
    for name in names:
        value = _get_optional_env(name)
        if value:
            return value, name

    joined = ", ".join(names)
    raise ValueError(f"One of these environment variables is required: {joined}")


def _get_env(name: str, default: str) -> str:
    value = _get_optional_env(name)
    if value is None:
        return default
    return value


def _get_first_env(names: tuple[str, ...], default: str) -> str:
    for name in names:
        value = _get_optional_env(name)
        if value is not None:
            return value
    return default


def _get_optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None

    normalized = value.strip().strip("'").strip('"')
    if normalized == "":
        return None
    return normalized


def _get_positive_int(name: str, default: int) -> int:
    raw = _get_optional_env(name)
    if raw is None:
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _get_bool_env(name: str, default: bool) -> bool:
    raw = _get_optional_env(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _normalize_webhook_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        return "/telegram/webhook"
    if normalized.startswith("/"):
        return normalized
    return f"/{normalized}"


def _normalize_target_chat_id(value: str) -> str:
    normalized = value.strip().strip("'").strip('"')
    if normalized.startswith("https://t.me/") or normalized.startswith("http://t.me/"):
        stripped = normalized.removeprefix("https://t.me/").removeprefix("http://t.me/")
        clean = stripped.split("?", 1)[0].strip("/")
        parts = [part for part in clean.split("/") if part]
        if len(parts) >= 2 and parts[0] == "c" and parts[1].isdigit():
            return f"-100{parts[1]}"
        username = parts[0].strip() if parts else ""
        if username and not username.startswith("+"):
            return f"@{username.lstrip('@')}"
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", normalized):
        return f"@{normalized}"
    return normalized


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def _load_text_file(file_path: str) -> str:
    path = Path(file_path).expanduser().resolve()
    try:
        content = path.read_text("utf-8").strip()
    except OSError as exc:
        raise ValueError(f"Could not read prompt file: {path}") from exc

    if not content:
        raise ValueError(f"Prompt file is empty: {path}")
    return content


def _load_text_file_with_fallback(file_path: str, *, fallback: str, warning_key: str) -> str:
    try:
        return _load_text_file(file_path)
    except ValueError as exc:
        LOGGER.warning(
            "Prompt fallback activated",
            extra={
                "component": "config.prompts",
                "event": "prompt_fallback_used",
                "context": {"prompt_key": warning_key, "path": file_path, "reason": str(exc)},
            },
        )
        return fallback


def _load_tag_catalog(file_path: str) -> tuple[str, ...]:
    path = Path(file_path).expanduser().resolve()
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Could not read tag catalog file: {path}") from exc

    tags = tuple(line.strip() for line in lines if line.strip() and not line.strip().startswith("//"))
    if not tags:
        raise ValueError(f"Tag catalog file is empty: {path}")
    return tags


def _load_tag_catalog_with_fallback(file_path: str, *, fallback: tuple[str, ...], warning_key: str) -> tuple[str, ...]:
    try:
        return _load_tag_catalog(file_path)
    except ValueError as exc:
        LOGGER.warning(
            "Tag catalog fallback activated",
            extra={
                "component": "config.prompts",
                "event": "prompt_fallback_used",
                "context": {"prompt_key": warning_key, "path": file_path, "reason": str(exc)},
            },
        )
        return fallback


def _load_post_types_config_with_fallback(
    file_path: str,
) -> tuple[tuple[PostTypeConfig, ...], TopicLayoutConfig, TopicDiversityConfig]:
    try:
        return _load_post_types_config(file_path)
    except ValueError as exc:
        LOGGER.warning(
            "Post types config fallback activated",
            extra={
                "component": "config.post_types",
                "event": "post_types_fallback_used",
                "context": {"path": file_path, "reason": str(exc)},
            },
        )
        return _default_post_types_config()


def _load_post_types_config(
    file_path: str,
) -> tuple[tuple[PostTypeConfig, ...], TopicLayoutConfig, TopicDiversityConfig]:
    path = Path(file_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text("utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read post types config file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in post types config file: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Post types config root must be an object")

    raw_post_types = payload.get("post_types")
    if not isinstance(raw_post_types, list):
        raise ValueError("post_types must be an array")

    post_types: list[PostTypeConfig] = []
    for item in raw_post_types:
        if not isinstance(item, dict):
            continue
        post_type_id = str(item.get("id", "")).strip()
        if not post_type_id:
            continue
        if post_type_id == "myth_vs_fact":
            continue
        label = str(item.get("label", post_type_id)).strip() or post_type_id
        prompt_hint = str(item.get("prompt_hint", label)).strip() or label
        enabled = bool(item.get("enabled", True))
        priority = int(item.get("priority", 100))
        post_types.append(
            PostTypeConfig(
                id=post_type_id,
                label=label,
                enabled=enabled,
                priority=priority,
                prompt_hint=prompt_hint,
            )
        )

    active = sorted((pt for pt in post_types if pt.enabled), key=lambda pt: (pt.priority, pt.id))
    if not active:
        raise ValueError("No active post types in config")

    raw_layout = payload.get("layout", {})
    if not isinstance(raw_layout, dict):
        raw_layout = {}
    buttons_per_row = max(1, int(raw_layout.get("buttons_per_row", 3)))
    topic_count = max(1, int(raw_layout.get("topic_count", 9)))
    candidate_count = max(topic_count, int(raw_layout.get("candidate_count", max(topic_count * 2, 20))))
    topic_layout = TopicLayoutConfig(
        buttons_per_row=buttons_per_row,
        topic_count=topic_count,
        candidate_count=candidate_count,
    )

    raw_diversity = payload.get("diversity", {})
    if not isinstance(raw_diversity, dict):
        raw_diversity = {}
    similarity_threshold = float(raw_diversity.get("similarity_threshold", 0.72))
    exploration_ratio = float(raw_diversity.get("exploration_ratio", 0.3))
    recent_window = max(1, int(raw_diversity.get("recent_window", 100)))
    topic_diversity = TopicDiversityConfig(
        similarity_threshold=max(0.0, min(1.0, similarity_threshold)),
        exploration_ratio=max(0.0, min(1.0, exploration_ratio)),
        recent_window=recent_window,
    )
    return tuple(active), topic_layout, topic_diversity


def _default_post_types_config() -> tuple[tuple[PostTypeConfig, ...], TopicLayoutConfig, TopicDiversityConfig]:
    try:
        return _load_post_types_config(str(DEFAULT_POST_TYPES_CONFIG_PATH))
    except ValueError:
        post_types = (
            PostTypeConfig(
                id="educational_short",
                label="Короткий обучающий пост",
                enabled=True,
                priority=10,
                prompt_hint="Короткая практичная тема для аудитории, изучающей ML/AI.",
            ),
        )
        layout = TopicLayoutConfig(buttons_per_row=3, topic_count=9, candidate_count=20)
        diversity = TopicDiversityConfig(similarity_threshold=0.72, exploration_ratio=0.3, recent_window=100)
        return post_types, layout, diversity
