# backend

Django-монолит. Один процесс, внутри — модули, каждый разрезан на четыре слоя.

```
config/     композиционный корень: настройки, контейнер зависимостей, корневые URL
core/       то, что переиспользуют все модули (ошибки, обработчик исключений, права, схема)
apps/       сами модули; сейчас один — users
tests/      тесты
```

## Модуль

`apps/users` — образец для всех следующих:

```
users/
├── domain/           сущности, value objects, перечисления, доменные ошибки
├── application/      сценарии, порты (Protocol), DTO
├── infrastructure/   ORM-модели, репозитории, мапперы, миграции
└── presentation/     views, сериализаторы, urls, admin
```

Правило зависимостей: `presentation → application → domain`, а `infrastructure` реализует
порты, объявленные в `application`. Внутренний слой никогда не импортирует внешний.

`domain` не импортирует Django. Это не стилистика: именно поэтому `tests/test_domain.py`
запускается без базы и без настроек.

## Где что лежит

| Нужно                         | Файл                                           |
| ----------------------------- | ---------------------------------------------- |
| связать сценарий с реализацией | `config/container.py`                          |
| добавить эндпоинт             | `apps/<модуль>/presentation/`                   |
| новое бизнес-правило          | `apps/<модуль>/domain/` + сценарий в `application/` |
| новая таблица                 | `apps/<модуль>/infrastructure/models.py`        |
| общий формат ошибки           | `core/presentation/exception_handler.py`        |

Миграции живут в `infrastructure/migrations/` рядом с моделями — это прописано в
`MIGRATION_MODULES`, а не достаётся по умолчанию.

## Новый модуль

1. `apps/<имя>/` с четырьмя слоями и `apps.py`.
2. Добавить в `INSTALLED_APPS` и в `MIGRATION_MODULES` (`config/settings/base.py`).
3. Подключить `presentation/urls.py` в `config/urls.py`.
4. Собрать сценарии в `config/container.py`.

Ошибки модуля наследуются от четырёх базовых из `core/domain/errors.py`
(`ValidationError`, `AccessDeniedError`, `NotFoundError`, `ConflictError`) — обработчик
исключений сам переведёт их в 400/403/404/409. Сценарий никогда не выбирает HTTP-код.

## Локальный запуск без Docker

Нужен Python 3.13 и работающий Postgres.

```bash
pip install -e . --group dev        # или: uv sync
export POSTGRES_HOST=localhost
python manage.py migrate
python manage.py runserver          # settings по умолчанию — config.settings.dev
```

`config.settings.dev` разрешает CORS с `http://localhost:5173` и выключает `Secure` у cookie:
по обычному HTTP браузер такую cookie просто не сохранит, и вход «пройдёт», а следующий запрос
придёт анонимным.

## Проверки

```bash
pytest -q
ruff check . && ruff format --check .
python manage.py makemigrations --check --dry-run
```
