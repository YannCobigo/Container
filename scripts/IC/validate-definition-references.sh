#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for def in "$ROOT"/definitions/*.def; do
    echo "Checking $(basename "$def")"
    grep -q '^Bootstrap:' "$def" || { echo "Missing Bootstrap" >&2; exit 1; }
    grep -q '^From:' "$def" || { echo "Missing From" >&2; exit 1; }
done

for req in "$ROOT"/requirements/*.txt; do
    test -s "$req"
done

echo "Definition/reference checks passed."
