#!/usr/bin/env bash
set -euo pipefail

export APPTAINER_CACHEDIR=/serv/Container/cache
export APPTAINER_TMPDIR=/serv/Container/tmp

ROOT=/serv/Container
DEF="$ROOT/definitions/IC"
SBX="$ROOT/sandboxes/IC"
IMG="$ROOT/images/IC"
LOG="$ROOT/logs/IC"

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
#build_sandbox Rocky-8.10_00_base Rocky-8.10_00_base.def
#build_sandbox Rocky-8.10_10_cuda13.3_node1 Rocky-8.10_10_cuda13.3_node1.def
#build_sandbox Rocky-8.10_20_tex Rocky-8.10_20_tex.def
#build_sandbox Rocky-8.10_30_dev Rocky-8.10_30_dev.def
#build_sandbox Rocky-8.10_40_python-core Rocky-8.10_40_python-core.def
#build_sandbox Rocky-8.10_41_python-neuro Rocky-8.10_41_python-neuro.def
#build_sandbox Rocky-8.10_50_neuro Rocky-8.10_50_neuro.def

# Example final production artifacts:
build_sif Rocky-8.10_IC Rocky-8.10_50_neuro
#build_sif Rocky-8.10_50_tex Rocky-8.10_50_tex
