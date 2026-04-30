# PROJECT GUIDE

## Why This Project Exists

`Posting Assistant Bot` нужен для команд и авторов, которым важно быстро готовить контент и не терять контроль качества перед публикацией.

## What The Bot Helps With

1. Создание постов для канала:
- вход: текст или voice;
- выход: готовый черновик с кнопками `Approve/Reopen`.

2. Модерация публикации:
- `Approve` отправляет пост в целевой чат;
- `Reopen` генерирует новую версию по тому же входу.

3. Ответы на комментарии через review-flow:
- бот принимает комментарии из source-чата;
- создаёт карточку в review-чате;
- модератор генерирует ответ и отправляет его reply в исходный чат.

## Core Flows

### Post Draft Flow

1. Владелец отправляет идею.
2. Бот генерирует черновик.
3. Владелец выбирает `Approve` или `Reopen`.

### Topic Generator Flow

1. Нажатие `🎯 Генератор тем`.
2. Генерация 4 тем.
3. Выбор темы кнопкой `1..4`.
4. Генерация черновика по выбранной теме.

### Comment Reply Flow

1. Новое сообщение приходит в `COMMENT_SOURCE_CHAT_ID`.
2. Бот создаёт review-карточку в `COMMENT_REVIEW_CHAT_ID`.
3. Модератор нажимает `Сгенерировать`.
4. Бот показывает черновик ответа.
5. Модератор нажимает `Отправить`.

## Prompt System (Customizable)

Поведение генерации управляется файлами промптов. Всё настраивается под вашу нишу и стиль.

Переменные:
- `ROLE_PROMPT_PATH`
- `STYLE_PROMPT_PATH`
- `THEMES_PROMPT_PATH`
- `TAGS_PATH`

### Baseline Templates

`role` (пример):
```text
You are a Telegram posting assistant.
Goal: turn rough ideas into clear publish-ready drafts.
```

`style` (пример):
```text
Write concise, practical, and human.
Short paragraphs, no bureaucratic tone.
```

`themes` (пример):
```text
Focus on topics relevant to the target audience and product domain.
Avoid off-topic news and personal diary style.
```

`tags` (пример):
```text
#product
#engineering
#update
```

Override policy:
- меняете файлы промптов;
- перезапускаете инстанс;
- бот начинает генерировать в новом стиле/рамках.

## Deployment And Security Notes

- Все токены и ID задаются только через `.env`.
- Не храните секреты в Git.
- Для публичного репозитория используйте только placeholders в документации.

## Runtime Notes

- Если `TAGS_PATH` пустой/отсутствует, генерация работает без тегов.
- Если `ffmpeg` недоступен, voice-сценарий помечается как not ready.
- При дублирующем polling-процессе бот логирует понятную ошибку о второй инстанции.
