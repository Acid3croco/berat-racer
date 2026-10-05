#!/bin/sh
# Send a map package to the Windows PC over the local network (docs/unreal-roadmap.md, A9).
#
#   tools/package/send_to_pc.sh <user@pc-address> <package folder> [destination folder on the PC, default C:/berat/package]
#
# The PC needs its OpenSSH server on (Settings > System > Optional features > OpenSSH Server, then in an admin PowerShell:
# Start-Service sshd; Set-Service sshd -StartupType Automatic). Windows has tar built in, so the package goes as one tar stream
# over ssh (no rsync on Windows). SHA256SUMS is written first; on the PC, check it with tools/package/verify_on_pc.ps1.
set -eu
[ $# -ge 2 ] || { sed -n '2,9p' "$0"; exit 2; }
target=$1
package=$(cd "$2" && pwd)
dest=${3:-C:/berat/package}
name=$(basename "$package")

if [ ! -f "$package/SHA256SUMS" ] || [ -n "$(find "$package" -type f -newer "$package/SHA256SUMS" ! -name SHA256SUMS | head -1)" ]; then
    echo "checksums of $name ..."
    (cd "$package" && find . -type f ! -name SHA256SUMS | sort | xargs shasum -a 256 > SHA256SUMS)
fi
echo "$name: $(du -sh "$package" | cut -f1) -> $target:$dest/$name"
ssh "$target" "mkdir \"$dest\" 2>NUL & exit 0"
tar -C "$(dirname "$package")" -cf - "$name" | ssh "$target" "tar -xf - -C \"$dest\""
echo "sent. On the PC: powershell -File verify_on_pc.ps1 $dest/$name"
