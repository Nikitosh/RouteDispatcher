#!/bin/sh
# Демо целиком: планы шести участков, пять событий на Востоке, страница диспетчера на http://localhost:8080.
# Запуск из корня проекта: scripts/demo.sh (или docker compose up). Без OSRM работает на кэшах из cache/.
set -e
cd "$(dirname "$0")/.."
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
FP="$PY -m dispatch"
for r in Восток Югоцентр Юго-восток Север Запад Северо-восток; do
  t=3; [ "$r" = "Юго-восток" ] && t=10
  $FP plan "data/demo/$r заявки.xlsx" --name "$r" -b "data/demo/$r бригады.xlsx" -o "out/$r" --time $t
done
$FP replan out/Восток --at 14:00 --unavailable "Бригада 4" -o out/Восток_недоступна
$FP replan out/Восток --at 13:00 --cancel 74198 -o out/Восток_отмена
$FP replan out/Восток --at 13:30 --urgent "55.7155, 37.7625" --type подключение --window 16:00-18:00 --id 90001 --district Кузьминки -o out/Восток_заявка
$FP replan out/Восток --at 11:30 --urgent "55.7400, 37.7200" --type авария --id 90002 --district Лефортово -o out/Восток_авария_1130
$FP replan out/Восток --at 14:00 --urgent "55.7400, 37.7200" --type авария --id 90003 --district Лефортово -o out/Восток_авария_1400
$FP site out/Восток out/Югоцентр out/Юго-восток out/Север out/Запад out/Северо-восток \
  out/Восток_недоступна out/Восток_отмена out/Восток_заявка out/Восток_авария_1130 out/Восток_авария_1400 -o site
[ "$1" = "--no-serve" ] || exec $FP serve site --port "${PORT:-8080}"
