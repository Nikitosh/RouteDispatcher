#!/bin/sh
# Папка для Vercel: страница диспетчера с пересчётом на сервере (dispatch/api.py) — без OSRM и Docker.
# Нужны готовые результаты scripts/demo.sh в out/, запущенный OSRM (сетка новых точек) и Docker (сборка решателей под
# Linux x86_64). Результат — папка vercel/; выкладка: cd vercel && vercel deploy (регион fra1 — в vercel.json).
set -e
cd "$(dirname "$0")/.."
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
OUT=vercel
rm -rf "$OUT"; mkdir -p "$OUT/bin"
$PY -m dispatch site out/Восток out/Восток_лучшие out/Восток_5на2 out/Восток_по_фактам out/Югоцентр out/Юго-восток out/Север out/Запад out/Северо-восток \
  out/Восток_разворот out/Восток_недоступна out/Восток_отмена out/Восток_задержка out/Восток_перенос_окна out/Восток_поломка out/Восток_заявка out/Восток_авария_1400 \
  --scenarios scripts/scenarios.json -o "$OUT/public" --api
docker run --rm --platform linux/amd64 -v "$PWD/solver:/src:ro" -v "$PWD/$OUT/bin:/out" alpine:3.20 sh -c \
  'apk add --no-cache g++ >/dev/null && cd /src && for b in plan baseline fleet_bound; do
     g++ -std=c++17 -O2 -march=x86-64-v2 -static -Isrc -pthread -o /out/$b src/$b.cpp || exit 1; done'
rsync -a --exclude __pycache__ --exclude 'tools/' dispatch "$OUT/"
mkdir -p "$OUT/data/traffic" && cp data/traffic/here_grid.npz "$OUT/data/traffic/"   # пробки те же, что в плане
cp deploy_tpl/app.py deploy_tpl/requirements.txt deploy_tpl/vercel.json deploy_tpl/.python-version deploy_tpl/.vercelignore "$OUT/"
du -sh "$OUT" "$OUT"/*
