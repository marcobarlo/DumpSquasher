# DSH headless: build with and without `diagrun_build`

Same **LMCache-Ascend** C++ target twice under DeepSeek Harness **headless**,
with **stock dsh-base output bounding**:

1. **Tool on** — `diagrun_build` (compact JSON).
2. **Tool off** — bash. DSH does **not** put the full 239 KB dump in context.

Stock bounds (`dsh-base` 0.1.1-rc.2):

| Layer | Default | What the model sees |
|---|---|---|
| bash stdout | **64 KB tail** (`maxOutputBytes`) | last 64 KB of make/`g++` |
| spill | **50 KB** inline (`maxInlineBytes`) | head+tail preview + spill path |
| pruner | **8192** chars, 4096 head + 1024 tail | only if context is under pressure / overflow |

One non-default: `bash-sandbox.timeoutMs: 600000` in
`~/.dsh/profiles/headless/cordis.patch.yml`. Stock is **60 s**; this rebuild
takes ~3.5 min. Spill and pruner stay at defaults.

Target: `cache_kernels_host_stub_obj` in container `vllm-ascend-dsv4-lmcache`
(generated `host_stub.cpp` ~279 KB; verbose rebuild ~239 KB). Script:
[`lmcache_host_stub_rebuild.sh`](lmcache_host_stub_rebuild.sh). Do not edit
LMCache-Ascend sources.

## 0. One-time env

```bash
export PATH="$HOME/.local/node/bin:$HOME/src/dsh-app/node_modules/.bin:$PATH"
export PYTHONPATH="$HOME/RCA_agent_tool/src"
export VLLM_API_KEY=local
export CHOKIDAR_USEPOLLING=1
export DSH_PERMISSION_MODE=danger-full-access

curl -sf http://127.0.0.1:8001/v1/models | python3 -m json.tool | head
docker exec vllm-ascend-dsv4-lmcache cmake --version | head -1
```

```bash
rm -rf /tmp/dsh-with-tool /tmp/dsh-no-tool
mkdir -p /tmp/dsh-with-tool /tmp/dsh-no-tool
dsh plugin --profile headless add "$HOME/RCA_agent_tool/integrations/dsh-diagrun"
```

```bash
"$HOME/RCA_agent_tool/docs/lmcache_host_stub_rebuild.sh"
```

## 1. Tool **enabled**

```bash
cd /tmp/dsh-with-tool
dsh --profile headless "$(cat <<'EOF'
Rebuild the LMCache-Ascend C++ target cache_kernels_host_stub_obj.

1. Run the build with the diagrun_build tool only. command:
   /home/m00926961/RCA_agent_tool/docs/lmcache_host_stub_rebuild.sh
   cwd: /tmp/dsh-with-tool
   Do not use bash to compile.
2. Use compact JSON (status, exit_code, roots). Call diagrun_get_raw only
   if roots and unclassified are both empty.
3. Stop when exit_code is 0. Do not edit files under LMCache-Ascend.

Reply with: run_id, exit_code, whether status is passed.
EOF
)"
```

## 2. Tool **disabled** (stock spill / pruner)

```bash
dsh plugin --profile headless remove dsh-diagrun
export DSH_PERMISSION_MODE=danger-full-access
cd /tmp/dsh-no-tool
dsh --profile headless "$(cat <<'EOF'
Rebuild the LMCache-Ascend C++ target cache_kernels_host_stub_obj.

1. There is no diagrun_build tool. Run this with the bash tool:
   /home/m00926961/RCA_agent_tool/docs/lmcache_host_stub_rebuild.sh
   The rebuild takes several minutes. Do not background it.
2. Stop when the command exits 0. Do not edit LMCache-Ascend sources.

Reply with: exit code and a one-line summary of the build.
EOF
)"
```

Expect a **50 KB** bash result (spill preview) with
`[output truncated]` and a spill path, not 239 KB inline.

## 3. Compare

Sessions: `9f94cfd6` (with) and `eec2accc` (without). Same rebuild script.

| | With `diagrun_build` | Without (bash, stock DSH) |
|---|---|---|
| Build | `status: passed`, `exit_code: 0` | cmake exit 0 |
| Model-visible tool result | **298 B** compact (`raw.bytes: 239241`) | **50,000 B** spill preview (omitted 14,439 B; bash already tailed at 64 KB) |
| Pruner (8k) | n/a (already small) | did not fire (50 KB still fit in 32k) |
| Full-context words | **4,897** | **10,326** (bash block 5,771) |

![Model-visible context with vs without diagrun_build](context-with-vs-without.png)

Regenerate: `.venv-docs/bin/python3 docs/plot_context_ab.py`.

## 4. Restore the plugin

```bash
dsh plugin --profile headless add "$HOME/RCA_agent_tool/integrations/dsh-diagrun"
```

## Failures worth checking

- Bash killed at 60s: stock `bash-sandbox.timeoutMs` is 60s; this rebuild needs
  the 10 min override in `cordis.patch.yml`.
- `docker exec` denied: `DSH_PERMISSION_MODE=danger-full-access`.
- Container `vllm-ascend-dsv4-lmcache` not running.
