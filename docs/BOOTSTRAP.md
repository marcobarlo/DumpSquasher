# Bootstrap: 300-case DSH A/B on b04 (910B3-04)

This tree is a copy of the **diagrun** work from `910B3-03` (`192.168.0.42`).
You are on **b04** (`910B3-04`). NPUs **0–3** are reserved for this experiment’s
vLLM. Do **not** use NPUs 4–7. Do **not** mutate LMCache-Ascend.

## Your job

Implement and run the 300-case A/B in
[docs/ab300-plan.md](ab300-plan.md) (same as Cursor plan
`300-case A/B harness`). Always run **both arms** (with `diagrun_build` and
without). Checkpoint. Plot a **new** figure `docs/ab300-fix-and-context.png`.

Do not overwrite `docs/context-with-vs-without.png`.

## Paths on this host

| What | Path |
|---|---|
| diagrun repo | `/home/m00926961/RCA_agent_tool` |
| DSH 0.1.1-rc.2 | `/home/m00926961/src/dsh-app` |
| Node 22 | `/home/m00926961/.local/node` |
| DSH settings | `/home/m00926961/.dsh/settings.yaml` |
| Headless profile | `/home/m00926961/.dsh/profiles/headless/` |
| Qwen3-8B weights | `/data/models/qwen3-8b` |
| Plan | `docs/ab300-plan.md` |
| This file | `docs/BOOTSTRAP.md` |

```bash
export PATH="$HOME/.local/node/bin:$HOME/src/dsh-app/node_modules/.bin:$PATH"
export PYTHONPATH="$HOME/RCA_agent_tool/src"
export VLLM_API_KEY=local
export CHOKIDAR_USEPOLLING=1
export DSH_PERMISSION_MODE=danger-full-access
```

Plugin: `dsh plugin --profile headless add $HOME/RCA_agent_tool/integrations/dsh-diagrun`

vLLM for DSH: `http://127.0.0.1:8001/v1` model id `qwen3-8b`, context **32768**,
tool parser **hermes** (not `qwen3_xml`).

## vLLM on NPUs 0–3 (blocker on first copy)

`qwen3-8b` fits on **one** 910B3. Prefer:

```text
ASCEND_RT_VISIBLE_DEVICES=0
--tensor-parallel-size 1 --max-model-len 32768 --gpu-memory-utilization 0.90
--enable-auto-tool-choice --tool-call-parser hermes --port 8001
Image: quay.io/ascend/vllm-ascend:v0.22.1rc1
Mount: /data/models/qwen3-8b -> /workspace/models/qwen3-8b
```

If you must use 0–3: `ASCEND_RT_VISIBLE_DEVICES=0,1,2,3` and
`--tensor-parallel-size 4`. **On copy day, NPUs 2–3 already had ~57 GB
`VLLMWorker_TP` processes** — do not kill them. NPUs **0 and 1 were empty**.
Use NPU **0** (TP=1) unless 2–3 are free.

**Docker on b04 was `root:root` on `/var/run/docker.sock`** (this user is not
in group `docker`). Starting the container needs root or a docker ACL. Script:
[scripts/b04_vllm_qwen3.sh](../scripts/b04_vllm_qwen3.sh).

## Product (diagrun)

Wrap C++ builds, store the full log under a ULIL run id, return **compact
roots** to the coding agent. Integrations: DSH plugin
`integrations/dsh-diagrun`, Pi, MCP. Agent ops: `python3 -m diagrun call OP`.

### Context budget (why A/B exists)

- Compact payload must beat raw when `raw.bytes >= 512`.
- Session bloat was **call volume**, not pretty JSON. `collapse_parse_recovery`
  defaults **true** on the agent path. Roots include a bounded `snippet`.
- **Stock DSH does not put a 239 KB dump in the prompt.** Defaults:
  60 s bash timeout, **64 KB stdout tail**, **50 KB spill** (head+tail + path),
  **8k-char pruner** (4096 head + 1024 tail) on pressure/overflow.
- The LMCache host_stub figure that showed 239 KB inline **raised those caps**.
  Realistic A/B: keep stock spill/pruner; only raise bash timeout to 10 min
  (`bash-sandbox.timeoutMs: 600000` in `~/.dsh/profiles/headless/cordis.patch.yml`).

### Measure model-visible context

Use **last tool result per callId** (post-spill / post-pruner replacement), plus
system + schemas + user. Word count = Unicode `\w+`. See
`docs/plot_context_ab.py`.

## What already ran (do not repeat blindly)

| Experiment | Result |
|---|---|
| DSH A/B `missing_member` fixture | Tiny dump (~280 B). Schema tax of four `diagrun_*` tools beat savings. Agent-on used more calls. |
| LMCache `host_stub` verbose rebuild (~239 KB) with caps raised | Compact 298 B vs 239 KB dump; no-tool `CONTEXT_WINDOW_EXCEEDED`. **Not stock DSH.** |
| Same rebuild, stock spill/pruner | Bash result **50 KB spill preview**; compact 298 B. Totals ~4.9k vs ~10.3k words. Both exit 0. No “fix” — green rebuild. |
| Injected faults in LMCache `csrc/utils.cpp` **tool-only** | **Invalid:** user now requires A/B always. Syntax + type mismatch **fixed**; missing_member + undeclared **not** (agent added API instead of reverting the inject). Roots were complete (~500 B); failure was repair policy, not missing context. First attempt failed `EACCES` mkdir in root-owned `csrc/` — generated cases must live in a **user-writable scratch copy**. |

Gold for 300 cases: `make` exit 0 **and** `./app` still matches vanilla return code.

## 300-case grid (implement this)

10 kinds × 5 sizes × 6 variants = 300. Generated C++ only.

Kinds: `syntax_cascade`, `missing_member`, `undeclared_identifier`,
`wrong_function_signature`, `missing_include`, `redeclaration`,
`template_instantiation`, `concept_failure`, `linker_undefined_symbol`,
`shared_header_fanout`.

Sizes: 1 / 4 / 16 / 64 TUs, plus **verbose** (~50–150 KB dummy source).

Always: copy to `/tmp/ab300/<id>/with` and `/tmp/ab300/<id>/without`. Arm A
plugin on; arm B plugin off (prefer a second profile `headless-no-diagrun`).
Append `experiments/ab300/results.jsonl`. Resume on `(case_id, arm)`.

~20–50 hours sequential vs one vLLM. Do not parallelize DSH.

## Files to add (not copied as code yet)

- `experiments/ab300/generate_cases.py`
- `experiments/ab300/run_ab.py`
- `experiments/ab300/measure_session.py`
- `experiments/ab300/plot_ab300.py`

## Source machine reference

910B3-03, user `m00926961`, vLLM historically in container
`vllm-ascend-dsv4-lmcache` (`quay.io/ascend/vllm-ascend:v0.22.1rc1`), DSH
headless, qwen3-8b, `CHOKIDAR_USEPOLLING=1` (inotify ENOSPC).
