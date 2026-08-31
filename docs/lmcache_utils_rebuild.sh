#!/bin/bash
# Compile only csrc/utils.cpp.o inside vllm-ascend-dsv4-lmcache (no c_ops link).
set -euo pipefail
CONTAINER="${LMCACHE_CONTAINER:-vllm-ascend-dsv4-lmcache}"
exec docker exec "$CONTAINER" bash -lc "
  cd /workspace/LMCache-Ascend/build &&
  rm -f csrc/CMakeFiles/c_ops.dir/utils.cpp.o &&
  /usr/bin/gmake -f csrc/CMakeFiles/c_ops.dir/build.make csrc/CMakeFiles/c_ops.dir/utils.cpp.o
"
