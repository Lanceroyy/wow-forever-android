#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$REPO_DIR/app/src/main/assets/bundled_components"
PINS="$REPO_DIR/tools/components.sha256"
if command -v sha256sum >/dev/null; then
    sha256=(sha256sum)
else
    sha256=(shasum -a 256)
fi
RELEASE_URL="${COMPONENTS_URL:-https://github.com/jaredgei/wow-forever-android/releases/download/components-v1}"

if [[ "${1:-}" == "--verify-only" ]]; then
    (cd "$DEST" && tr -d '\r' < "$PINS" | "${sha256[@]}" -c -)
    exit
fi

mkdir -p "$DEST"
temp=""
trap '[[ -z "$temp" ]] || rm -f -- "$temp"' EXIT
while read -r digest file; do
    [[ "$digest" =~ ^[a-f0-9]{64}$ && "$file" =~ ^[a-zA-Z0-9._-]+[.](zip|wcp)$ ]]
    if [[ -f "$DEST/$file" ]] && printf '%s  %s\n' "$digest" "$DEST/$file" | "${sha256[@]}" -c >/dev/null; then
        echo "verified $file"
        continue
    fi
    temp="$(mktemp "$DEST/.component.XXXXXXXX")"
    if [[ -n "${1:-}" && -f "$1/$file" ]]; then
        cp -- "$1/$file" "$temp"
    else
        url="$RELEASE_URL/$file"
        if [[ "$file" == "turnip-V32-RP6sched.zip" ]]; then
            url="https://raw.githubusercontent.com/arusiasotto/wow-forever-a840/main/driver/$file"
        fi
        curl --proto '=https' --proto-redir '=https' -fL --retry 3 --output "$temp" "$url"
    fi
    printf '%s  %s\n' "$digest" "$temp" | "${sha256[@]}" -c >/dev/null
    mv -f -- "$temp" "$DEST/$file"
    temp=""
    echo "verified $file"
done < <(tr -d '\r' < "$PINS")

(cd "$DEST" && tr -d '\r' < "$PINS" | "${sha256[@]}" -c -)
