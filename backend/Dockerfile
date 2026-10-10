# syntax=docker/dockerfile:1

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libpq is not needed by psycopg[binary] (it ships its own), but curl is what the Compose
# healthcheck calls.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Dependencies are installed from the manifest alone, so a source-only edit reuses this layer.
COPY pyproject.toml ./
RUN python -m pip install --upgrade pip \
 && python -m pip install \
      "django==6.1.2" \
      "djangorestframework==3.18.3" \
      "drf-spectacular==0.30.0" \
      "django-cors-headers==4.9.0" \
      "django-environ==0.14.0" \
      "psycopg[binary]==3.3.6" \
      "gunicorn==26.2.0" \
      "whitenoise==6.12.0" \
      "symspellpy==6.10.0" \
      "httpx==0.28.1" \
      "opensearch-py==2.8.0"

# Энкодер отдельным слоем, и `torch` — из индекса CPU-колёс. Обычный `pip install torch`
# притащил бы сборку с CUDA: несколько гигабайт ради вычислений на GPU, которого на стенде
# нет. Слой идёт после прикладных зависимостей, поэтому правка версии Django его не
# пересобирает, а пересобирать его дороже всего остального в образе вместе.
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.6.0" \
 && python -m pip install "sentence-transformers==3.4.1"

# Модель вшивается в образ, а не скачивается при первом запросе — почему, написано в самом
# скрипте. rubert-tiny2: 29 млн параметров, 312 измерений, ~120 МБ — то самое «предпочтение
# лёгким и быстрым» из условий хакатона, и на CPU это единицы миллисекунд на запрос.
ARG EMBEDDING_MODEL=cointegrated/rubert-tiny2
ENV EMBEDDING_MODEL_PATH=/opt/models/encoder
COPY docker/fetch_encoder.py /tmp/fetch_encoder.py
RUN python /tmp/fetch_encoder.py "$EMBEDDING_MODEL" "$EMBEDDING_MODEL_PATH" \
 && rm /tmp/fetch_encoder.py \
 && chmod -R a+rX /opt/models

# Dev tooling, off by default. The production image stays free of a test runner and a linter;
# the development stack turns this on so `pytest` and `ruff` can be run inside the same
# container that serves the app, against the same Python and the same installed versions.
# Without it the checks in the README could not be run at all: the published image has no
# pytest, and `docker compose exec backend pytest` fails with "executable file not found".
ARG INSTALL_DEV=0
RUN if [ "$INSTALL_DEV" = "1" ]; then \
      python -m pip install "ruff==0.16.10" "pytest==9.1.1" "pytest-django==4.14.0"; \
    fi

COPY . .

# Non-root: the process only needs to read the code and write staticfiles. The home directory
# is real because gunicorn's control server writes under $HOME; with the default `/nonexistent`
# it logs a permission error on every boot.
RUN adduser --system --group --home /home/app app \
 && mkdir -p /app/staticfiles /home/app \
 && chown -R app:app /app /home/app
ENV HOME=/home/app
USER app

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
