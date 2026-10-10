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
      "lxml==6.1.3" \
      "snowballstemmer==3.1.1" \
      "jsonschema==4.26.0" \
      "numpy==2.5.3"

# Эмбеддер отдельным слоем, и `torch` — из индекса CPU-колёс. Обычный `pip install torch`
# притащил бы сборку с CUDA: несколько гигабайт ради вычислений на GPU, которого в контейнере
# нет. Слой идёт после прикладных зависимостей: правка версии Django его не пересобирает.
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.14.1" \
 && python -m pip install "sentence-transformers==6.1.0"

# Эмбеддер вшивается в образ, а не скачивается при первом запросе: на защите сети может не
# оказаться в самый неподходящий момент. Qwen3-Embedding-0.6B (архитектура §5): 0,6 млрд
# параметров, 1024 измерения, сильный на русском; на CPU — десятки миллисекунд на запрос.
# Векторы карточек семейств посчитаны заранее и лежат в kb/ — в работе кодируется только запрос.
ARG EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
ENV STE_EMBEDDER_PATH=/opt/models/qwen3-embedding-0.6b
COPY docker/fetch_encoder.py /tmp/fetch_encoder.py
RUN python /tmp/fetch_encoder.py "$EMBEDDING_MODEL" "$STE_EMBEDDER_PATH" \
 && rm /tmp/fetch_encoder.py \
 && chmod -R a+rX /opt/models

# Dev tooling, off by default. The production image stays free of a test runner and a linter;
# the development stack turns this on so `pytest` and `ruff` can be run inside the same
# container that serves the app, against the same Python and the same installed versions.
ARG INSTALL_DEV=0
RUN if [ "$INSTALL_DEV" = "1" ]; then \
      python -m pip install "ruff==0.16.10" "pytest==9.1.1" "pytest-django==4.14.0" "openpyxl==3.1.5"; \
    fi

COPY . .

# Non-root: the process only needs to read the code and write staticfiles and the LLM cache.
# The home directory is real because gunicorn's control server writes under $HOME.
RUN adduser --system --group --home /home/app app \
 && mkdir -p /app/staticfiles /app/var/llm_cache /home/app \
 && chown -R app:app /app /home/app
ENV HOME=/home/app
USER app

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
