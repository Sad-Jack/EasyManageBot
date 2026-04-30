# Application Services

Этот каталог содержит use-case оркестрацию и бизнес-правила уровня приложения.

## Границы сервисов
- `posting_service.py`:
  - генерация поста через LLM-порт;
  - сохранение pending-поста;
  - переходы pending-поста (attach preview, publish, delete).
- `topics_service.py`:
  - предложение/регенерация темы;
  - подготовка данных для генерации поста из темы;
  - статусные переходы темы.
- `comments_service.py`:
  - создание состояния комментария для owner-нотификации;
  - генерация ответа на комментарий;
  - переходы статусов комментария (ignored/failed/sent и т.д.).

## Что должно попадать в сервисы
- orchestration use-case;
- бизнес-переходы статусов;
- подготовка данных между шагами сценария.

## Что не должно попадать в сервисы
- Telegram transport (send/edit/delete/update handlers);
- bootstrap/wiring приложения;
- детали polling/webhook запуска;
- SQL-схема и миграции.
