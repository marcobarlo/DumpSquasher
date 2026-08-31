#!/bin/bash
# Start qwen3-8b vLLM-Ascend for DSH on b04. Prefer NPU 0 (TP=1).
# NPUs 2-3 were occupied by other VLLMWorker_TP (~57GB) at copy time.
# Requires docker access (root or docker group).
set -euo pipefail
IMAGE="${IMAGE:-quay.io/ascend/vllm-ascend:v0.22.1rc1}"
NAME="${NAME:-vllm-qwen3-ab300}"
DEVICES="${ASCEND_RT_VISIBLE_DEVICES:-0}"
TP="${TP:-1}"
PORT="${PORT:-8001}"
MODEL_HOST="${MODEL_HOST:-/data/models/qwen3-8b}"
docker rm -f "$NAME" 2>/dev/null || true
# shellcheck disable=SC2086
dev_args=""
IFS=',' read -ra ids <<< "$DEVICES"
for i in "${ids[@]}"; do
  dev_args="$dev_args --device /dev/davinci${i}"
done
exec docker run -d --name "$NAME" --net=host --ipc=host \
  --device /dev/davinci_manager --device /dev/devmm_svm --device /dev/hisi_hdc \
  $dev_args \
  -v /usr/local/dcmi:/usr/local/dcmi \
  -v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
  -v /usr/local/Ascend/driver:/usr/local/Ascend/driver \
  -v /etc/ascend_install.info:/etc/ascend_install.info \
  -v "$MODEL_HOST:/workspace/models/qwen3-8b:ro" \
  -e ASCEND_RT_VISIBLE_DEVICES="$DEVICES" \
  "$IMAGE" \
  bash -lc "source /usr/local/Ascend/ascend-toolkit/set_env.sh;
    source /usr/local/Ascend/nnal/atb/set_env.sh;
    vllm serve /workspace/models/qwen3-8b \
      --served-model-name qwen3-8b --host 0.0.0.0 --port $PORT \
      --tensor-parallel-size $TP --max-model-len 32768 \
      --gpu-memory-utilization 0.90 --trust-remote-code \
      --enable-auto-tool-choice --tool-call-parser hermes"
