#!/bin/bash
# Downloads the generated world (map) of a GitHub release and unpacks it into world/ (or the folder given as $1).
# A release ships the map in two zips (GitHub caps a release file at 2 GiB): berat-map-1.zip holds berat/ (world files and half the
# chunks), berat-map-2.zip holds berat-2/chunks (the other half). The game reads them side by side; here they are put together.
# Usage: tools/fetch_world.sh [target dir] [release tag, default: latest]       (releases up to v0.5 shipped berat-world-berat70.zip.part-*)
set -euo pipefail
repo=Acid3croco/berat-racer
root=$(cd "$(dirname "$0")/.." && pwd)
dest=${1:-$root/world}
tag=${2:-latest}
if [ "$tag" = latest ]; then base=https://github.com/$repo/releases/latest/download; else base=https://github.com/$repo/releases/download/$tag; fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

for part in berat-map-1.zip berat-map-2.zip; do
    echo "downloading $part"
    curl -fL --progress-bar -o "$tmp/$part" "$base/$part"
    unzip -q "$tmp/$part" -d "$tmp/map"
    rm "$tmp/$part"
done
find "$tmp/map/berat-2/chunks" -type f -print0 | xargs -0 sh -c 'mv "$@" "$0"' "$tmp/map/berat/chunks"     # ~25,000 files: in batches

if [ -e "$dest" ]; then echo "$dest exists: moving it to $dest.old"; rm -rf "$dest.old"; mv "$dest" "$dest.old"; fi
mkdir -p "$(dirname "$dest")"
mv "$tmp/map/berat" "$dest"
echo "world ready in $dest ($(ls "$dest/chunks" | wc -l | tr -d ' ') chunk files)"
