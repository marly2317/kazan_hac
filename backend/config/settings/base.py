"""Settings shared by every environment.

Anything environment-specific is read from the environment with a safe default, and the two
overlays (``dev``, ``prod``) only tighten what is set here.
"""

from __future__ import annotations

from pathlib import Path

import environ
from corsheaders.defaults import default_headers as default_cors_headers

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])


def _origins(raw: str) -> list[str]:
    """Parse a comma-separated origin list, tolerating stray whitespace and trailing slashes."""
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


INSTALLED_APPS = [
    # The admin is an internal back-office for staff, not part of the product surface: the API is
    # what the SPA talks to. It exists so roles can be assigned and data inspected without a
    # shell.
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    # Required by the admin: it reports the outcome of every save through the messages framework.
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Full-text search and trigram similarity live here (SearchVector, TrigramSimilarity).
    "django.contrib.postgres",
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "apps.users.apps.UsersConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.ktru.apps.KtruConfig",
    "apps.autofill.apps.AutofillConfig",
]

AUTH_USER_MODEL = "users.User"

# The admin is the only server-rendered login in the project — the SPA authenticates over the
# API. Without this, signing in at /admin/login/ lands on Django's default /accounts/profile/,
# which is not routed and 404s.
LOGIN_REDIRECT_URL = "/admin/"
LOGIN_URL = "/admin/login/"

# Migrations live inside each module's infrastructure layer, next to the ORM models.
MIGRATION_MODULES = {
    "users": "apps.users.infrastructure.migrations",
    "catalog": "apps.catalog.infrastructure.migrations",
    "ktru": "apps.ktru.infrastructure.migrations",
    "autofill": "apps.autofill.infrastructure.migrations",
}

MIDDLEWARE = [
    # First, so that a rejected preflight still carries the CORS headers.
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Serves the admin's own CSS and JS. Django only serves static files itself under
    # `runserver`, and this project runs on gunicorn, so without this the admin renders unstyled.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="app"),
        "USER": env("POSTGRES_USER", default="app"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="app"),
        "HOST": env("POSTGRES_HOST", default="postgres"),
        "PORT": env.int("POSTGRES_PORT", default=5432),
        # Reuse connections across requests; without this every request pays a TCP + TLS
        # handshake against Postgres.
        "CONN_MAX_AGE": env.int("POSTGRES_CONN_MAX_AGE", default=60),
        "CONN_HEALTH_CHECKS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- session and CSRF ---------------------------------------------------------------------
# The SPA is served from the same origin as the API (nginx routes /api/ to Django and everything
# else to the static bundle), so the session cookie is a same-origin cookie and `Lax` is enough
# to keep it off cross-site requests.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=60 * 60 * 24 * 14)
SESSION_COOKIE_SAMESITE = env("SESSION_COOKIE_SAMESITE", default="Lax")
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
# Slide the expiry on activity, so an open tab is not signed out mid-use.
SESSION_SAVE_EVERY_REQUEST = True

CSRF_COOKIE_HTTPONLY = False  # the SPA must read the token to echo it back in a header
CSRF_COOKIE_SAMESITE = env("CSRF_COOKIE_SAMESITE", default="Lax")
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
CSRF_TRUSTED_ORIGINS = _origins(env("CSRF_TRUSTED_ORIGINS", default=""))
# A rejected request must still answer the SPA in the one error envelope it parses.
CSRF_FAILURE_VIEW = "core.presentation.csrf.csrf_failure"

# Only needed when the SPA runs on the Vite dev server, i.e. on another origin; in Compose both
# sit behind one nginx and this list stays empty.
CORS_ALLOWED_ORIGINS = _origins(env("CORS_ALLOWED_ORIGINS", default=""))
CORS_ALLOW_CREDENTIALS = True
# The SPA sends JSON with the CSRF token echoed in a header, so that header must be allow-listed.
CORS_ALLOW_HEADERS = (*default_cors_headers, "x-csrftoken")
CORS_PREFLIGHT_MAX_AGE = env.int("CORS_PREFLIGHT_MAX_AGE", default=3600)

# nginx terminates TLS, so Django learns the original scheme from the forwarded header. Without
# it, `request.is_secure()` is False behind the proxy and secure cookies are never set.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Closed by default: a new view is private until it says otherwise.
    "DEFAULT_PERMISSION_CLASSES": ["core.presentation.permissions.IsAuthenticatedUser"],
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
    "EXCEPTION_HANDLER": "core.presentation.exception_handler.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Kazan Hack API",
    "DESCRIPTION": "Аутентификация и профиль пользователя.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    # Without the override the enum is emitted once per serializer that repeats the choices.
    "ENUM_NAME_OVERRIDES": {"RoleEnum": "apps.users.domain.enums.ROLE_CHOICES"},
}

# --- поисковый движок ----------------------------------------------------------------------
#
# OpenSearch — **производный** индекс: источник истины остаётся в Postgres, а движок
# перестраивается из него командой `manage.py index_ktru`. Отсюда два следствия, на которые
# опирается весь код ниже: потеря индекса не теряет данных, а рассогласование лечится
# переиндексацией, а не миграцией.
#
# Пустая строка выключает движок, и поиск возвращается на Postgres. Это не «аварийный режим»,
# а то, чем пользуется `manage.py bench_retrieval`: сравнить два движка на одном стенде можно
# только если оба доступны одновременно.
OPENSEARCH_URL = env("OPENSEARCH_URL", default="").rstrip("/")
# Префикс имён индексов. Отдельной переменной, потому что замер на другом наборе данных — это
# другой индекс рядом с рабочим, а не второй кластер.
OPENSEARCH_INDEX_PREFIX = env("OPENSEARCH_INDEX_PREFIX", default="ktru")
# Короткий: движок стоит перед полем ввода, и запрос, не ответивший за пять секунд, уже
# обошёлся дороже, чем стоит его результат.
OPENSEARCH_TIMEOUT = env.float("OPENSEARCH_TIMEOUT", default=5.0)

# --- энкодер для семантической части поиска ------------------------------------------------
#
# Путь к модели, вшитой в образ (см. `backend/Dockerfile`), а не имя на HuggingFace: сеть на
# стенде не гарантирована, и загрузка при первом запросе — это отказ в самый неподходящий
# момент.
#
# `EMBEDDING_ENABLED=0` оставляет поиск чисто лексическим. Это нужно затем же, зачем выключение
# движка: вклад семантики в качество измеряется её выключением, а не рассуждением о нём.
EMBEDDING_MODEL_PATH = env("EMBEDDING_MODEL_PATH", default="/opt/models/encoder")
EMBEDDING_ENABLED = env.bool("EMBEDDING_ENABLED", default=True)

# --- search and spelling correction --------------------------------------------------------
# The spelling pipeline degrades on purpose: with no key the LLM stage is simply not added, and
# correction falls back to the keyboard-layout and dictionary stages, which need nothing
# external.
# The hackathon rules forbid proprietary LLM APIs (OpenAI, Gemini, Claude "и другие подобные
# сервисы") and allow only open models, with a preference for light, fast ones and for Russian
# routers. So no vendor and no model is named here: point `LLM_BASE_URL` at any
# OpenAI-compatible endpoint serving an open model — a local vLLM or Ollama, timeweb, selectel.
# Empty by default, which leaves the deterministic stages to do the work.
LLM_BASE_URL = env("LLM_BASE_URL", default="").rstrip("/")
LLM_MODEL = env("LLM_MODEL", default="")
LLM_API_KEY = env("LLM_API_KEY", default="")
# Short on purpose: this sits in front of a form field. A model that has not answered in a few
# seconds has already cost more than the correction is worth.
LLM_TIMEOUT = env.float("LLM_TIMEOUT", default=4.0)
# Hybrid reasoning models spend the token budget thinking and return no content at all. Off by
# default; set to 0 only for a server that rejects the unknown `reasoning` field.
LLM_DISABLE_REASONING = env.bool("LLM_DISABLE_REASONING", default=True)

# --- сбор пробелов справочника --------------------------------------------------------------
#
# Необязательная возможность: сервис запоминает, какие слова из описаний он не смог объяснить
# определённой позицией КТРУ, и показывает накопленное в админке. Это заметка тем, кто ведёт
# справочник, — пользователю о ней не сообщается ничего.
#
# Выключается одной переменной: сбор ничего не добавляет в ответ и ничего не меняет в разборе,
# поэтому его отключение не влияет ни на один пользовательский сценарий.
COLLECT_REFERENCE_GAPS = env.bool("COLLECT_REFERENCE_GAPS", default=True)

# Correction results and the catalogue vocabulary are cached here. The default is per-process,
# so each gunicorn worker keeps its own copy — correct, just less effective. Point
# `CACHE_URL` at Redis to share one.
CACHES = {
    "default": env.cache_url(
        "CACHE_URL", default="locmemcache://spellcheck?timeout=604800&max_entries=10000"
    )
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
