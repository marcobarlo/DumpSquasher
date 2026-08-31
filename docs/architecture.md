# Architecture

## What diagrun is

diagrun is a **local command-execution layer** for coding agents. It runs
`make` / `ninja` / `cmake --build` / `g++` itself, captures stdout and stderr,
and hands the agent a small JSON object: status, exit code, a ULID `run_id`,
and a pointer to the raw log.

It is **not**:

- a proxy that intercepts the model's HTTP/OpenAI traffic
- a sidecar that wraps every `bash` call automatically
- an LLM that summarizes compiler output

The model still talks to vLLM (or any other provider) over the normal chat API.
When it wants a C++ build, it emits a **tool call**. The agent runtime
(Pi, DeepSeek Harness, or any MCP client) executes that tool by spawning
diagrun on this machine, then feeds the JSON result back as a tool-result
message.

```text
user prompt
    │
    ▼
agent runtime  ──chat/completions──►  local vLLM (Qwen3-8B, …)
    │  ▲
    │  │  tool_calls: [{ name: "diagrun_build", arguments: { command, cwd } }]
    │  │  tool result JSON (compact; not the 80 KB dump)
    ▼  │
diagrun tool  ──spawn──►  make / ninja / g++
    │
    ▼
run store  (~/.local/share/diagrun/runs/<ULID>/)
```

`diagrun_build` returns compact **roots** (file, line, message, kind). Call
`diagrun_get_raw` only when both `roots` and `unclassified` are empty.
Encode agent JSON without pretty-print. Omit inject/reducer/hint-to-fetch.

What landed in model context (DSH A/B on LMCache-Ascend `cache_kernels_host_stub_obj`):

![What entered model context with vs without diagrun_build](context-with-vs-without.png)

Sessions and how to reproduce: [DSH headless A/B](dsh-headless-example.md).
Regenerate the figure with `docs/plot_context_ab.py`.

## Two entry points

| Path | Who uses it | Capture |
|------|-------------|---------|
| CLI wrapper `diagrun [--] COMMAND…` | Humans, scripts | Live passthrough of stdout/stderr; original exit code |
| Tool ops `diagrun_build` / `get_raw` / `show` / `get_diagnostic` | Models | No live passthrough; JSON only |

Both share `run_command()` and the same store. The CLI is a transparent
wrapper. The tools are the agent-facing API.

```text
python -m diagrun -- make          # CLI: tee to terminal + store
python -m diagrun call build       # JSON in/out (what plugins spawn)
python -m diagrun mcp              # MCP stdio server over the same ops
python -m diagrun show --last      # retrieve stored run
```

## Core pipeline

```text
argv + cwd + config
        │
        ▼
   inject (optional)          codegen-neutral flags only
        │                     -fdiagnostics-format=json
        ▼                     -fdiagnostics-color=never
   capture                    pipes + select; stdout ≠ stderr
        │                     events.jsonl preserves interleaving
        ▼
   store                      ULID run dir, last pointer, GC
        │
        ▼
   reduce (partial)           policy exists; GCC/Clang parsers are open
        │
        ▼
   compact result             status, run_id, raw.ref [, roots]
```

Injection does not change generated code. Make wraps `CC`/`CXX` and leaves
makefile `CXXFLAGS` alone. CMake configure uses `CMAKE_*_COMPILER_LAUNCHER`;
`--build` / Ninja use PATH shims and Clang `CCC_OVERRIDE_OPTIONS`.

Pipes are used instead of a PTY so the two streams stay separable. The child
sees `isatty() == False`.

## Layout

```text
src/diagrun/
  cli.py                 wrapper + show + mcp + call
  config.py              inject / collapse-parse-recovery
  ids.py                 ULID run ids
  diagnostics/model.py   Diagnostic, DiagnosticGroup, BuildResult
  diagnostics/compiler.py GCC/Clang JSON + text
  diagnostics/buildsys.py Make/Ninja/collect2 suppression
  reducer/pipeline.py    fingerprint, group, persist diagnostics.json
  render/json.py         compact agent payload (no pretty-print)
  exec/runner.py         spawn + capture
  exec/capture.py        independent stdout/stderr + events.jsonl
  exec/inject.py         compiler/Make/CMake/Ninja flag injection
  store/runs.py          filesystem store + retention
  reducer/policy.py      parse-recovery collapse policy (default off)
  integrations/api.py    tool_build / get_raw / show / get_diagnostic
  integrations/mcp_server.py   MCP stdio

integrations/dsh-diagrun/     DeepSeek Harness plugin (defineTool)
plugins/diagrun/              Pi extension + Agent Plugins MCP package
fixtures/cpp_failures/        failing GCC/Make corpora
```

Store default: `$DIAGRUN_STORE` or `$XDG_DATA_HOME/diagrun`, else
`~/.local/share/diagrun`. Retention: `DIAGRUN_MAX_RUNS` (100),
`DIAGRUN_MAX_BYTES` (512 MiB).

Each run:

```text
<store>/runs/<ULID>/
  meta.json
  stdout.bin
  stderr.bin
  events.jsonl
  wrappers/          (inject shims, when used)
  diagnostics.json   (when parsers have run)
<store>/last
```

## How the model reaches diagrun

Shared Python ops live in `diagrun.integrations.api`. Every agent surface
is a thin adapter over `dispatch(op, params)`.

```text
                    ┌─────────────────────────────┐
                    │  diagrun.integrations.api    │
                    │  build | get_raw | show |    │
                    │  get_diagnostic              │
                    └─────────────┬───────────────┘
           ┌──────────────┬───────┴────────┬──────────────┐
           ▼              ▼                ▼              ▼
     diagrun call    MCP stdio      DSH plugin       Pi extension
     JSON stdin      tools/call     defineTool       registerTool
```

DeepSeek Harness and Pi do **not** speak MCP for this path. They load a
native tool and spawn `python3 -m diagrun call OP` with JSON on stdin.
MCP (`diagrun mcp` / `plugins/diagrun/mcp.json`) is for other clients.

Typical Pi / DSH turn:

1. Runtime sends chat request to vLLM, including the `diagrun_build` schema
   in `tools`.
2. vLLM (with `--enable-auto-tool-choice --tool-call-parser hermes`) returns
   a structured `tool_calls` entry, not free-text XML.
3. Runtime runs the local tool; diagrun executes `make` and writes the store.
4. Runtime posts a `tool` / `toolResult` message with the compact JSON.
5. Model continues (often a short `run_id` + `exit_code` reply).

The LLM never executes the compiler. It only chooses arguments. diagrun is
the process that actually builds.

## What is not wired yet

Plan tasks 13–15 (real-repo corpus, over/under-reduction log, first
dependency-graph rule pack beyond fingerprint grouping) are still open.
Parsers, Make/Ninja suppression, grouping, and compact agent JSON are
implemented. Agents should act on `roots` and call `diagrun_get_raw` only
when that list is empty.
