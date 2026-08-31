#!/bin/bash
# Rebuild one LMCache-Ascend AscendC host-stub object inside the existing
# vllm-ascend-dsv4-lmcache container. The stub translation unit is ~279 KB of
# generated C++; a verbose rebuild is ~240 KB of compiler/make output.
set -euo pipefail
CONTAINER="${LMCACHE_CONTAINER:-vllm-ascend-dsv4-lmcache}"
OBJ="third_party/kvcache-ops/CMakeFiles/cache_kernels_host_stub_obj.dir/__/__/auto_gen/cache_kernels/host_stub.cpp.o"
exec docker exec "$CONTAINER" bash -lc "
  cd /workspace/LMCache-Ascend/build &&
  rm -f ${OBJ} &&
  cmake --build . --target cache_kernels_host_stub_obj --verbose
"
