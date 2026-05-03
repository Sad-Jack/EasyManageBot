# Posting Assistant Bot

## 1. Очень быстрый старт
1. `python3 -m venv .venv && source .venv/bin/activate`
2. `pip install -e .`
3. `cp .env.example .env`
4. заполните минимум: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`, `TARGET_CHAT_ID`
5. `python -m posting_assistant_bot.main`

Перейти к подробной настройке: [Блок 3](#3-подробная-настройка-проекта)

---

## 2. Описание проекта, фичи и архитектура

### Что это
Telegram-бот для подготовки постов и модерации ответов на комментарии.

### Основные фичи
- Генерация черновика поста из текста/voice/audio/video_note.
- Кнопки модерации черновика: `Approve` и `Reopen`.
- Генератор тем: выбор темы кнопками `1..N` и генерация поста по выбранной теме.
- Review-flow комментариев:
  - входящий комментарий из source-чата;
  - карточка в review-чате;
  - генерация и отправка reply.
- Prompt-driven поведение через файлы:
  - `role_prompt.txt`
  - `style_prompt.txt`
  - `themes_prompt.txt`
  - `tags.txt`
  - `post_types.json`

### Архитектура (кратко)
- `posting_assistant_bot/main.py`: runtime/transport слой (Telegram handlers, routing, wiring).
- `posting_assistant_bot/application/services/*`: use-case оркестрация.
- `posting_assistant_bot/application/ports.py`: контракты/порты.
- `posting_assistant_bot/storage/database.py`: SQLite storage.
- `posting_assistant_bot/claude/orchestrator.py`: LLM orchestration.
- `posting_assistant_bot/transcription.py`: локальная транскрибация медиа.

---

## 3. Подробная настройка проекта

### 3.1 Обязательные переменные
- `TELEGRAM_BOT_TOKEN`
- `OWNER_TELEGRAM_ID`
- `TARGET_CHAT_ID`

### 3.2 Рекомендуемые переменные
- `COMMENT_SOURCE_CHAT_ID`
- `COMMENT_REVIEW_CHAT_ID`
- `COMMENT_REPLY_MODE=bot|user`
- `BOT_MODE=polling|webhook`
- `UI_LANGUAGE=ru|en`
- `LOG_LEVEL`, `LOG_FORMAT`, `LOG_TO_FILE`, `LOG_FILE_PATH`

### 3.3 Voice/transcription
- `VOICE_TRANSCRIPTION_ENABLED=true|false`
- `VOICE_TRANSCRIPTION_PROVIDER=mlx-whisper`
- `LOCAL_TRANSCRIBE_MODEL`
- `VOICE_MAX_DURATION_SECONDS`
- `VOICE_TMP_DIR`

Требование: `ffmpeg` должен быть доступен в `PATH`.

### 3.4 Prompt и типы постов
- `ROLE_PROMPT_PATH`
- `STYLE_PROMPT_PATH`
- `THEMES_PROMPT_PATH`
- `TAGS_PATH`
- `POST_TYPES_CONFIG_PATH`

Если prompt-файл отсутствует, бот использует fallback и пишет warning в лог.

### 3.5 Запуск webhook режима
- `BOT_MODE=webhook`
- `TELEGRAM_WEBHOOK_BASE_URL=https://your-domain`
- `TELEGRAM_WEBHOOK_PATH=/telegram/webhook`
- `PORT=3000`

### 3.6 Структура репозитория
- `posting_assistant_bot/` — код приложения
- `документы/tasks/` — активные задачи
- `документы/bugs/` — активные баги
- `документы/archive/` — архив закрытых задач и багов (игнорируется git)