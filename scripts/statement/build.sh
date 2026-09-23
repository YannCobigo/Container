#!/bin/sh
set -euo pipefail

export APPTAINER_CACHEDIR=/serv/Container/cache
export APPTAINER_TMPDIR=/serv/Container/tmp

ROOT=/serv/Container
DEF="$ROOT/definitions"
SBX="$ROOT/sandboxes"
IMG="$ROOT/images"
LOG="$ROOT/logs"

# Run from /serv/Container. --reproducible normalizes SIF creation metadata where supported.
IMAGE=${1:-${IMG}/statement/pdfbank.sif}
SANDB=${1:-${SBX}/statement/pdfbank.sandbox}
DEF=${2:-${DEF}/statement/statement.def}
#exec apptainer build --reproducible "$IMAGE" "$DEF"
apptainer build --reproducible "$SANDB" "$DEF"
