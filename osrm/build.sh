#!/bin/sh
# Графы OSRM для Москвы и юга области (машина, велосипед, пешком). Нужны только для новых адресов:
# демо-данные работают на кэшах координат, матриц и линий из cache/.
# Требуется Docker и ~8 ГБ памяти; занимает 15–30 минут.
set -e
cd "$(dirname "$0")"
[ -f cfd.osm.pbf ] || curl -L -o cfd.osm.pbf https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf
# Москва и юг области (Кашира, Ступино, Домодедово)
docker run --rm -v "$PWD:/d" -w /d stefda/osmium-tool osmium extract -b 36.8,54.7,38.4,56.1 cfd.osm.pbf -o msk.osm.pbf --overwrite
for p in car:car bicycle:bicycle foot:foot; do
  dir=${p%%:*}; prof=${p##*:}; mkdir -p "$dir"; cp msk.osm.pbf "$dir/"
  docker run --rm -v "$PWD/$dir:/data" osrm/osrm-backend osrm-extract -p /opt/$prof.lua /data/msk.osm.pbf
  docker run --rm -v "$PWD/$dir:/data" osrm/osrm-backend osrm-partition /data/msk.osrm
  docker run --rm -v "$PWD/$dir:/data" osrm/osrm-backend osrm-customize /data/msk.osrm
done
echo "Готово. Запуск: docker compose --profile osrm up"
