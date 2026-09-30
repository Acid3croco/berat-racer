#!/bin/sh
# Screenshots of one spot of a generated world with the built player: top, oblique and road-level views.
#   tools/shot.sh NAME X Z [WORLD_DIR] [OUT_DIR]      (X Z: local metres, as shown in the game's coordinate box)
# Needs Build/dev/BeratRacer.app (Unity batch build: BuildTools.BuildMac -out ../Build/dev/BeratRacer.app).
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
name=$1; x=$2; z=$3; world=${4:-$root/world_small}; out=${5:-$root/Build/shots}
mkdir -p "$out"
BERAT_WORLD=$world "$root"/Build/dev/BeratRacer.app/Contents/MacOS/* -batchmode -mute -notraffic -shotat "$x" "$z" -logFile /tmp/berat_shot.log >/dev/null 2>&1
for view in top oblique road; do mv "$root/Build/docs/shots/at_$view.png" "$out/${name}_$view.png"; done
grep -c "Exception" /tmp/berat_shot.log | sed 's/^/exceptions in log: /'
