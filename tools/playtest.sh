#!/bin/sh
# Headless driving tests of a world with a built player: the road benchmark (-roadtest) and the autopilot run (-autotest SECONDS).
#   tools/playtest.sh [WORLD_DIR] [SECONDS]      BERAT_APP=path/to/BeratRacer.app selects the player (default Build/dev)
# Prints the result lines of both runs.
root=$(cd "$(dirname "$0")/.." && pwd)
world=${1:-$root/world_small}; seconds=${2:-240}
app=${BERAT_APP:-$root/Build/dev/BeratRacer.app}
log=${TMPDIR:-/tmp}/berat_playtest
BERAT_WORLD=$world "$app"/Contents/MacOS/* -batchmode -mute -roadtest -logFile "$log.road.log" >/dev/null 2>&1
grep "BENCHMARK\|jolt RMS\|wheel-hop\|phantom stops\|INPUT:" "$log.road.log"
BERAT_WORLD=$world "$app"/Contents/MacOS/* -batchmode -mute -autotest "$seconds" -logFile "$log.auto.log" >/dev/null 2>&1
grep "RESULT\|\[traffic\]" "$log.auto.log" | tail -5
grep -c "Exception" "$log.road.log" "$log.auto.log" | sed 's/^/exceptions: /'
