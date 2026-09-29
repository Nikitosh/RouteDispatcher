#!/bin/sh
# Готовые графы OSRM (машина, велосипед, пешком) из релиза GitHub — вместо сборки build.sh (15–30 минут).
# Нужны только для новых адресов: демо работает на кэшах из cache/. После загрузки: docker compose --profile osrm up.
# Качает через gh, если он установлен и авторизован (нужно, пока репозиторий приватный), иначе через curl.
set -e
cd "$(dirname "$0")"
REPO=Nikitosh/RouteDispatcher
TAG=osrm-graphs-v1
FILES="osrm-car.tar.gz osrm-bicycle.tar.gz osrm-foot.tar.gz SHA256SUMS"

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh release download "$TAG" -R "$REPO" --clobber $(for f in $FILES; do printf -- '-p %s ' "$f"; done)
else
  for f in $FILES; do
    curl -fL -o "$f" "https://github.com/$REPO/releases/download/$TAG/$f"
  done
fi
if command -v sha256sum >/dev/null 2>&1; then sha256sum -c SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi
for f in osrm-car.tar.gz osrm-bicycle.tar.gz osrm-foot.tar.gz; do
  tar -xzf "$f" && rm "$f"
done
rm SHA256SUMS
echo "Готово: графы в osrm/car, osrm/bicycle, osrm/foot. Запуск: docker compose --profile osrm up"
