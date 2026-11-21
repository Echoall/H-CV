#!/usr/bin/env bash
set -euo pipefail

# Build the upstream Unix port and ulab on Linux or WSL.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="${ROOT}/.build/upy"
MICROPYTHON_REF="${MICROPYTHON_REF:-v1.24.1}"
ULAB_REF="${ULAB_REF:-6.5.2}"
JOBS="${JOBS:-2}"
MAKE="${MAKE:-make}"
PYTHON="${PYTHON:-python3}"

mkdir -p "${BUILD}"
if [ ! -d "${BUILD}/micropython/.git" ]; then
    git clone --filter=blob:none https://github.com/micropython/micropython.git \
        "${BUILD}/micropython"
fi
if [ ! -d "${BUILD}/ulab/.git" ]; then
    git clone --filter=blob:none https://github.com/v923z/micropython-ulab.git \
        "${BUILD}/ulab"
fi

git -C "${BUILD}/micropython" fetch --tags --depth=1 origin "${MICROPYTHON_REF}"
git -C "${BUILD}/micropython" checkout --detach "${MICROPYTHON_REF}"
git -C "${BUILD}/ulab" fetch --tags --depth=1 origin "${ULAB_REF}"
git -C "${BUILD}/ulab" checkout --detach "${ULAB_REF}"

"${MAKE}" -C "${BUILD}/micropython/ports/unix" submodules
"${MAKE}" -C "${BUILD}/micropython/ports/unix" -j"${JOBS}" \
    PYTHON="${PYTHON}" \
    USER_C_MODULES="${BUILD}/ulab"

cp "${BUILD}/micropython/ports/unix/build-standard/micropython" "${BUILD}/micropython-ulab"
echo "Built ${BUILD}/micropython-ulab"
