#!/bin/bash
# The game's own icon out of the APK, as a square picture that fills its canvas.
#
#   scripts/app-icon.sh <apk> <out.png>
#
# Up to 1.4.2 the APK's pvz2_icon.png was that picture (1200 pixels). Since 1.5.0 it is a small
# one with round corners, and the full picture is the middle of pvz2_icon_background.png: an
# Android launcher shows the middle two thirds of that layer, so those are taken here.
set -euo pipefail
apk="$1" out="$2"
tmp="$(mktemp -d)"
unzip -o -q "$apk" "res/mipmap-xxxhdpi/pvz2_icon.png" "res/mipmap-xxxhdpi/pvz2_icon_background.png" -d "$tmp"
icon="$tmp/res/mipmap-xxxhdpi/pvz2_icon.png" back="$tmp/res/mipmap-xxxhdpi/pvz2_icon_background.png"
width() { sips -g pixelWidth "$1" | awk '/pixelWidth/ { print $2 }'; }
if [ "$(width "$icon")" -ge 512 ]; then
    cp "$icon" "$out"
else
    side=$(( $(width "$back") * 2 / 3 ))
    sips -c "$side" "$side" "$back" --out "$out" >/dev/null
fi
