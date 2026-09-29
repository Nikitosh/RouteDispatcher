#!/bin/bash
# render.sh deck.pptx [first last] -> PDF рядом с deck + slide-NN.jpg в presentation_build/preview/
# Montserrat берётся из профиля LibreOffice в lo_profile/ (headless-режим не видит ~/Library/Fonts).
HERE="$(cd "$(dirname "$0")" && pwd)"
DECK="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
soffice -env:UserInstallation=file://$HERE/lo_profile --headless --convert-to pdf --outdir "$(dirname "$DECK")" "$DECK" >/dev/null 2>&1
PDF="${DECK%.*}.pdf"
if [ -n "$2" ]; then
  rm -rf "$HERE/preview"; mkdir -p "$HERE/preview"
  pdftoppm -jpeg -r 80 -f $2 -l $3 "$PDF" "$HERE/preview/slide"
fi
echo "$PDF"
