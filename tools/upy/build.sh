#!/usr/bin/env bash
set -euo pipefail

# Build a reproducible MicroPython unix binary with the ulab version used by
# the K230 firmware.  Run this script from a Linux host or WSL.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="${ROOT}/.build/upy"
MICROPYTHON_REF="${MICROPYTHON_REF:-v1.24.1}"
ULAB_REF="${ULAB_REF:-6.5.2}"
JOBS="${JOBS:-2}"
MAKE="${MAKE:-make}"
PYTHON="${PYTHON:-python3}"

mkdir -p "${BUILD}"
mkdir -p "${BUILD}/compat"
cat > "${BUILD}/compat/alloca.h" <<'EOF'
#include <malloc.h>
EOF
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

"${MAKE}" -C "${BUILD}/micropython" -j"${JOBS}" -C ports/unix submodules
"${MAKE}" -C "${BUILD}/micropython/ports/unix" -j"${JOBS}" \
    PYTHON="${PYTHON}" \
    USER_C_MODULES="${BUILD}/ulab/code" \
    CFLAGS_EXTRA="-I${BUILD}/compat -Wno-error -DULAB_VERSION=\\\"${ULAB_REF}\\\""

cp "${BUILD}/micropython/ports/unix/micropython" "${BUILD}/micropython-ulab"
echo "Built ${BUILD}/micropython-ulab"
