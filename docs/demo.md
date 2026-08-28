# Manual demo

Fixture: `fixtures/cpp_failures/missing_member` (`make` fails; missing member).

Requires `PYTHONPATH` pointing at `src`. For agent demos, Node 22+, Pi, DSH,
and a local OpenAI-compatible server (this host: vLLM-Ascend serving
`qwen3-8b` on `http://127.0.0.1:8000/v1`).

```bash
export PATH=~/.local/node/bin:~/src/agents/node_modules/.bin:~/src/dsh-app/node_modules/.bin:$PATH
export PYTHONPATH=~/RCA_agent_tool/src
export VLLM_API_KEY=local
export CHOKIDAR_USEPOLLING=1
cd ~/RCA_agent_tool
```

Confirm the model: `curl -s http://127.0.0.1:8000/v1/models`.

## 1. CLI (no LLM)

```bash
python3 -m diagrun -- make -C fixtures/cpp_failures/missing_member
python3 -m diagrun show --last
python3 -m diagrun show --last --raw | head
```

Expect exit code 2 and a ULID `run_id`. Streams still print live.

JSON tool op (what plugins spawn):

```bash
python3 -m diagrun call build <<'EOF'
{"command":"make","cwd":"/home/m00926961/RCA_agent_tool/fixtures/cpp_failures/missing_member"}
EOF
```

## 2. Pi

Configs: `~/.pi/agent/models.json` (provider `vllm`, model `qwen3-8b`).

```bash
pi -p --offline --no-session --provider vllm --model qwen3-8b \
  --thinking off --no-extensions --no-builtin-tools --tools diagrun_build \
  -e ./plugins/diagrun/dev.pi.agent/index.ts \
  -- "Use diagrun_build exactly once with command make and cwd /home/m00926961/RCA_agent_tool/fixtures/cpp_failures/missing_member. Then reply with only run_id and exit_code."
```

Interactive: drop `-p --offline --no-session` and the quoted prompt.

vLLM needs `--enable-auto-tool-choice --tool-call-parser hermes`. The
`qwen3_xml` parser left tool calls as text; hermes returns structured
`tool_calls`.

## 3. DeepSeek Harness

Configs: `~/.dsh/settings.yaml` (`agent-default-model` → `vllm` / `qwen3-8b`).
Plugin: `dsh plugin --profile headless add ./integrations/dsh-diagrun`.

```bash
dsh --profile headless \
  "Use the diagrun_build tool exactly once with command make and cwd /home/m00926961/RCA_agent_tool/fixtures/cpp_failures/missing_member. Then reply with only the run_id and exit_code."
```

`CHOKIDAR_USEPOLLING=1` avoids inotify `ENOSPC` on this host. Qwen3 may emit
a `<think>…</think>` block before the `run_id` and `2`.

## 4. Inspect

```bash
python3 -m diagrun show RUN_ID
python3 -m diagrun show --last --raw | head
```
