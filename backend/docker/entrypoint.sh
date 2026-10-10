#!/bin/sh
# Container entrypoint: bring the schema up to date, then serve.
#
# Migrations run here rather than in a separate job because this stack has one web replica. With
# more than one, move this to a dedicated one-shot service so two replicas cannot migrate at once.
set -eu

echo "Applying migrations…"
python manage.py migrate --noinput

echo "Collecting static files…"
python manage.py collectstatic --noinput --clear

if [ "${SEED_DEMO_USER:-0}" = "1" ]; then
  python manage.py seed_demo_user
fi

if [ "${SEED_CATALOG:-0}" = "1" ]; then
  python manage.py seed_catalog
fi

echo "Starting gunicorn on :8000…"
# Один воркер с потоками, а не несколько процессов: эмбеддер занимает 1–2 ГБ памяти в каждом
# процессе, а запрос большую часть времени ждёт ответа LLM по сети — потоки здесь дешевле.
# Таймаут — с запасом на два вызова LLM на бесплатном тарифе.
exec gunicorn config.wsgi:application \
  --config /app/gunicorn.conf.py \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-8}" \
  --worker-class gthread \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --access-logfile - \
  --error-logfile -
