#!/bin/sh
set -eu
if [ "$#" -ne 4 ]; then
  echo "usage: $0 URL SHA256 OUTPUT_FILE MODEL_VERSION" >&2
  exit 2
fi
URL=$1
EXPECTED=$2
OUT=$3
VERSION=$4
mkdir -p "$(dirname "$OUT")"
curl --fail --location --proto '=https' --tlsv1.2 "$URL" -o "$OUT.tmp"
printf '%s  %s\n' "$EXPECTED" "$OUT.tmp" | sha256sum -c -
mv "$OUT.tmp" "$OUT"
printf '{"file":"%s","version":"%s","sha256":"%s"}\n' "$(basename "$OUT")" "$VERSION" "$EXPECTED" > "${OUT}.manifest.json"
echo "Installed and verified $OUT"
