# Application Services

Сервисы в этом каталоге — слой use-case оркестрации.

## Сервисы
- `posting_service.py` — генерация/перегенерация и lifecycle pending-постов.
- `topics_service.py` — генерация тем и подготовка данных для поста по теме.
- `comments_service.py` — review-flow комментариев и статусы отправки reply.
- `runtime_read_model_service.py` — read-модель для runtime handlers (`status/reset/queue`) без прямого доступа из transport к БД.

## Границы
- Здесь не должно быть Telegram transport кода.
- Здесь не должно быть bootstrap/polling/webhook кода.
- Здесь не должно быть SQL schema/migration деталей.
- Инфраструктура должна использоваться через порты из `application/ports.py`.
