#!/usr/bin/env bash
# Прогнать описание через подбор КТРУ.
#
#   ./scripts/try.sh "молоток с длиной рукоятки 350 мм"
#   ./scripts/try.sh                       # прогнать набор демо-кейсов
#
# Переменные: BASE (по умолчанию http://localhost:8099), EMAIL, PASSWORD.
set -eu

BASE="${BASE:-http://localhost:8099}"
EMAIL="${EMAIL:-demo@example.com}"
PASSWORD="${PASSWORD:-demo-password-2026}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

# Сессионная авторизация: сначала CSRF-кука, затем вход.
curl -sS -c "$JAR" -o /dev/null "$BASE/api/v1/auth/csrf/"
CSRF="$(awk '/csrftoken/ {print $7}' "$JAR")"
code=$(curl -sS -b "$JAR" -c "$JAR" -o /dev/null -w '%{http_code}' \
  -H 'Content-Type: application/json' -H "X-CSRFToken: $CSRF" -H "Referer: $BASE/" \
  -X POST -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  "$BASE/api/v1/auth/login/")
[ "$code" = "200" ] || { echo "Вход не удался (HTTP $code). Поднят ли стек на $BASE?" >&2; exit 1; }

show() {
  printf '\n\033[1m═══ %s\033[0m\n' "$1"
  curl -sS -b "$JAR" --get --data-urlencode "description=$1" \
    "$BASE/api/v1/autofill/analyze/" | python3 "$(dirname "$0")/_format.py"
}

if [ $# -gt 0 ]; then
  show "$*"
else
  show "молоток с длиной рукоятки 350 мм"      # число + единица → ОКЕИ
  show "Куртка зимная из кожы женская"         # опечатки → словарь КТРУ
  show "rehnrf pbvyzz bp rj;b"                 # раскладка клавиатуры
  show "молотокслесарный"                      # склейка слов (стадия LLM), сужение 14 → 2
  show "молоток 350 попугаев"                  # несуществующая единица измерения
  show "куртка зимняя из кожи женская 48"      # несколько характеристик подряд
fi
