#!/usr/bin/env bash
set -euo pipefail

export APPTAINER_CACHEDIR=/serv/Container/cache
export APPTAINER_TMPDIR=/serv/Container/tmp

ROOT=/serv/Container
DEF="$ROOT/definitions/NeuroMap-DL"
SBX="$ROOT/sandboxes/NeuroMap-DL"
IMG="$ROOT/images/NeuroMap-DL"
LOG="$ROOT/logs/NeuroMap-DL"

#mkdir -p "$SBX" "$IMG" "$LOG"

build_sandbox() {
    local name="$1" def="$2"
    echo "==> Building sandbox: $name"
    apptainer build --sandbox "$SBX/$name.sandbox" "$DEF/$def"
}

build_sif() {
    local name="$1" sandbox="$2"
    echo "==> Converting sandbox to SIF: $name"
    apptainer build "$IMG/$name.sif" "$SBX/$sandbox.sandbox"
}

# Base -> CUDA -> Dev -> Python Core -> Python Neuro -> Neuro
# TeX intentionally branches from dev and is independent of neuro.
#build_sandbox NeuroMap-DL NeuroMap-DL.def

# Example final production artifacts:
build_sif NeuroMap-DL NeuroMap-DL

