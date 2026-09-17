#!/bin/bash
# Install diagrun (DumpSquasher) inside an Ascend vLLM container and put a
# cmake shim on PATH so LMCache-Ascend ``pip install`` kernel builds go
# through ``diagrun --format json``.
#
# Host:
#   CONTAINER=vllm-ascend-dsv4-lmcache bash scripts/install_in_container.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONTAINER="${CONTAINER:-${LMCACHE_CONTAINER:-vllm-ascend-dsv4-lmcache}}"
DEST="${DEST:-/opt/DumpSquasher}"
WRAP="${WRAP:-/opt/diagrun/bin}"

docker cp "${ROOT}/." "${CONTAINER}:${DEST}"
docker exec -u root "${CONTAINER}" bash -lc "
  set -euo pipefail
  python3 -m pip install -e '${DEST}'
  python3 -m diagrun install-wrappers --dir '${WRAP}'
  printf 'export PATH=%s:\$PATH\n' '${WRAP}' > /etc/profile.d/diagrun.sh
  if ! grep -q '/opt/diagrun/bin' /root/.bashrc 2>/dev/null; then
    printf '\\nexport PATH=%s:\$PATH\\n' '${WRAP}' >> /root/.bashrc
  fi
  command -v diagrun
  diagrun --version
"
echo "Installed diagrun in ${CONTAINER}. Prepend ${WRAP} to PATH for cmake --build wrapping."
