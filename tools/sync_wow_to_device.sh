#!/usr/bin/env bash
set -euo pipefail

SRC_ROOT="${WOW_SRC:-/Applications/World of Warcraft}"
DST_ROOT="${WOW_DST:-/storage/emulated/0/WoW Forever}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

command -v adb >/dev/null || export PATH="/opt/homebrew/share/android-commandlinetools/platform-tools:$PATH"
adb get-state >/dev/null
# Quote every path sent to the remote shell, including a literal single quote.
remote_quote() {
    local value="$1"
    printf "'%s'" "${value//\'/\'\\\'\'}"
}

list_local() {
    (cd "$SRC_ROOT/Data" && find . -type f ! -name shmem -exec stat -f '%z %N' {} + | sed 's| \./| |' | LC_ALL=C sort)
}

list_device() {
    adb shell "cd $(remote_quote "$DST_ROOT/Data") 2>/dev/null && find . -type f ! -name shmem -exec stat -c '%s %n' {} +" | sed 's| \./| |' | LC_ALL=C sort
}

adb shell -n "mkdir -p $(remote_quote "$DST_ROOT/Data") $(remote_quote "$DST_ROOT/_forever_")"
list_local > "$WORK/local.txt"
list_device > "$WORK/device.txt"

LC_ALL=C comm -23 "$WORK/local.txt" "$WORK/device.txt" | cut -d' ' -f2- > "$WORK/push.txt"
echo "Pushing $(wc -l < "$WORK/push.txt" | tr -d ' ') changed data files"
while IFS= read -r f; do
    adb shell -n "mkdir -p $(remote_quote "$DST_ROOT/Data/$(dirname "$f")")"
    adb push "$SRC_ROOT/Data/$f" "$DST_ROOT/Data/$f" < /dev/null > /dev/null
    echo "pushed $f"
done < "$WORK/push.txt"

# Keep an existing build record paired with its executable. The Android launcher
# can update them together through the authenticated Blizzard downloader.
if ! adb shell -n "test -f $(remote_quote "$DST_ROOT/.build.info")"; then
    adb push "$SRC_ROOT/.build.info" "$DST_ROOT/.build.info" > /dev/null
fi
echo "Data copy complete. Use the Android launcher to install or update the ARM64 client."
