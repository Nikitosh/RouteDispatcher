#!/bin/sh
# Выкладка на Vercel одной командой: страница диспетчера и экран инженера с сервером пересчёта (scripts/build_vercel.sh).
#   scripts/release.sh test   — preview тестового проекта route-dispatcher-api-test (по умолчанию)
#   scripts/release.sh prod   — продакшн основного проекта route-dispatcher-trdelnik
# Нужны готовые out/ (scripts/demo.sh), Docker (решатели под Linux) и vercel CLI с входом в аккаунт.
set -e
cd "$(dirname "$0")/.."
target=${1:-test}
case "$target" in
  test) project=route-dispatcher-api-test; flag="" ;;
  prod) project=route-dispatcher-trdelnik; flag="--prod" ;;
  *) echo "использование: scripts/release.sh [test|prod]" >&2; exit 2 ;;
esac
start=$(date +%s)
sh scripts/build_vercel.sh >/dev/null
cd vercel
vercel link --yes --project "$project" >/dev/null 2>&1
url=$(vercel deploy --yes $flag 2>/dev/null | grep -Eo 'https://[a-z0-9.-]+\.vercel\.app' | tail -1)
echo "$target: $url ($(( $(date +%s) - start )) с)"
