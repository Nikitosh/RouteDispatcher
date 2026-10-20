#!/bin/sh
# Демо целиком: планы шести участков, варианты и события Востока, страница диспетчера на http://localhost:8080.
# Запуск из корня проекта: scripts/demo.sh (или docker compose up). Без OSRM работает на кэшах из cache/.
set -e
cd "$(dirname "$0")/.."
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
FP="$PY -m dispatch"
rm -rf out/Восток_*                                    # события и варианты прошлого запуска — к старому плану
for r in Восток Югоцентр Юго-восток Север Запад Северо-восток; do
  # Юго-восток: 3 запуска по 30 с соревнуются с лучшим известным ответом решателя (data/demo/Юго-восток_ответ.json):
  # на этом участке поиск сильно зависит от сида. Восток — лучший из 5 запусков по 10 с
  t=3; [ "$r" = "Юго-восток" ] && t="30 --tries 3 --answer data/demo/Юго-восток_ответ.json"
  extra=--early; [ "$r" = "Восток" ] && t="10 --tries 5" && extra="--early --windows --curve --price --coverage --deficit"   # «что если», кривая, цена правил, карты
  [ "$r" = "Юго-восток" ] && extra="--early --coverage --deficit"
  [ "$r" = "Северо-восток" ] && extra="--early --price"
  $FP plan "data/demo/$r заявки.xlsx" --name "$r" -b "data/demo/$r бригады.xlsx" -o "out/$r" --time $t $extra
done
# режим «лучшие монтажники» по рейтингу и цена качества (парк и отзывы — python -m dispatch reviews Восток)
$FP plan "data/demo/Восток заявки.xlsx" --name "Восток — лучшие" -b "data/demo/Восток монтажники.xlsx" \
  --reviews "data/demo/Восток отзывы.csv" --best -o "out/Восток_лучшие"
# те же бригады по графику 5/2: смены 10:00–19:00 и 13:00–22:00 по очереди
$FP plan "data/demo/Восток заявки.xlsx" --name "Восток — 5/2" -b "data/demo/Восток бригады.xlsx" --schedule 5/2 -o "out/Восток_5на2" --time 60 --tries 5
# недоступна, отмена, задержка, перенос окна, поломка — заявки и бригады подбираются по свежему плану
$PY scripts/demo_events.py out/Восток
# «план учится»: история фактических длительностей → уточнённые коэффициенты сложности
$FP facts
$FP plan "data/demo/Восток заявки.xlsx" --name "Восток — по истории выездов" -b "data/demo/Восток бригады.xlsx" --facts data/demo/выезды.csv -o "out/Восток_по_фактам"
$FP replan out/Восток --at 13:30 --urgent "55.7050, 37.7900" --type подключение --window 18:00-20:00 --id 90001 --district Кузьминки -o out/Восток_заявка
$FP replan out/Восток --at 11:30 --urgent "55.7400, 37.7200" --type авария --id 90002 --district Лефортово -o out/Восток_авария_1130
$FP replan out/Восток --at 14:00 --urgent "55.7400, 37.7200" --type авария --id 90003 --district Лефортово -o out/Восток_авария_1400
# демо «Разворот»: старший инженер на машине едет к клиенту, авария позади — сценарий подбирается по свежему плану
# (нужны дома OSM data/osm/addr.geojsonseq, в git их нет — без них сценария на странице не будет)
if [ -f data/osm/addr.geojsonseq ]; then $PY scripts/find_turn_scenario.py out/Восток -o out/Восток_разворот || echo "Разворот не подобран"; fi
DIRS=""
for d in Восток Восток_лучшие Восток_5на2 Восток_по_фактам Югоцентр Юго-восток Север Запад Северо-восток Восток_разворот \
         Восток_недоступна Восток_отмена Восток_задержка Восток_перенос_окна Восток_поломка Восток_заявка Восток_авария_1400; do
  if [ -f "out/$d/plan.json" ]; then DIRS="$DIRS out/$d"; fi
done
$FP site $DIRS --scenarios scripts/scenarios.json -o site
[ "$1" = "--no-serve" ] || exec $FP serve site --port "${PORT:-8080}"
