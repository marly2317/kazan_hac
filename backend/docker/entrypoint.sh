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
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-3}" \
  --timeout "${GUNICORN_TIMEOUT:-60}" \
  --access-logfile - \
  --error-logfile -
