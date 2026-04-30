# Posting Assistant Bot

Telegram-бот для подготовки публикаций и ответов на комментарии: принимает идею, генерирует черновик через LLM, даёт модерацию через `Approve/Reopen` и отдельный review-flow для ответов.

Подробный гайд: [PROJECT_GUIDE.md](PROJECT_GUIDE.md)

## Install And Run

1. Создайте окружение:
```bash
python3 -m venv .venv
source .venv/bin/activate
```
2. Установите зависимости:
```bash
pip install -e .
```
3. Подготовьте конфиг:
```bash
cp .env.example .env
```
4. Заполните обязательные переменные в `.env`:
- `TELEGRAM_BOT_TOKEN`
- `OWNER_TELEGRAM_ID`
- `TARGET_CHAT_ID`
5. Запустите бота:
```bash
python -m posting_assistant_bot.main
```

## Configuration

Обязательные:
- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота.
- `OWNER_TELEGRAM_ID` — ID владельца, который имеет доступ к управляющим действиям.
- `TARGET_CHAT_ID` — чат/канал, куда публикуются апрувнутые посты.

Рекомендуемые:
- `COMMENT_SOURCE_CHAT_ID` — чат, откуда читать комментарии пользователей.
- `COMMENT_REVIEW_CHAT_ID` — чат, где модератор генерирует и отправляет ответы.
- `COMMENT_REPLY_MODE=bot|user` — режим отправки ответа (`bot` для MVP).
- `BOT_MODE=polling|webhook` — режим запуска.
- `LOG_LEVEL`, `LOG_FORMAT`, `LOG_TO_FILE`, `LOG_FILE_PATH` — настройки логирования.

Voice:
- `VOICE_TRANSCRIPTION_ENABLED`
- `VOICE_TRANSCRIPTION_PROVIDER=mlx-whisper`
- `LOCAL_TRANSCRIBE_MODEL`
- `VOICE_MAX_DURATION_SECONDS`
- `VOICE_TMP_DIR`

Prompt files:
- `ROLE_PROMPT_PATH`
- `STYLE_PROMPT_PATH`
- `THEMES_PROMPT_PATH`
- `TAGS_PATH`

Примечания:
- Если `TAGS_PATH` отсутствует или пустой, бот работает без тегов.
- Для voice-транскрибации нужен `ffmpeg` в `PATH`.
