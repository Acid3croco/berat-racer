#!/bin/bash
# Downloads the generated world of a GitHub release and unpacks it into world/ (or the folder given as $1).
# The world is split into parts (GitHub caps a release file at 2 GiB): berat-world-berat70.zip.part-aa, -ab, ...
# Usage: tools/fetch_world.sh [target dir] [release tag, default: latest]
set -euo pipefail
repo=Acid3croco/berat-racer
root=$(cd "$(dirname "$0")/.." && pwd)
dest=${1:-$root/world}
tag=${2:-latest}
if [ "$tag" = latest ]; then base=https://github.com/$repo/releases/latest/download; else base=https://github.com/$repo/releases/download/$tag; fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

curl -fL --progress-bar -o "$tmp/parts.txt" "$base/berat-world-berat70.parts.txt"
while read -r part; do
    echo "downloading $part"
    curl -fL --progress-bar -o "$tmp/$part" "$base/$part"
done < "$tmp/parts.txt"
cat "$tmp"/berat-world-berat70.zip.part-* > "$tmp/world.zip"

if [ -e "$dest" ]; then echo "$dest exists: moving it to $dest.old"; rm -rf "$dest.old"; mv "$dest" "$dest.old"; fi
mkdir -p "$dest"
unzip -q "$tmp/world.zip" -d "$dest"
echo "world ready in $dest ($(ls "$dest/chunks" | wc -l | tr -d ' ') chunk files)"
