#!/usr/bin/env python3
"""DSH + diagrun_build: can the agent fix injected LMCache compile errors?

Copies vanilla csrc files, injects one fault, runs headless DSH with
diagrun_build, then always restores vanilla.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from lmcache_error_injections import CASES, backup_vanilla, inject, restore_vanilla

ROOT = Path("/home/m00926961/RCA_agent_tool")
REBUILD = ROOT / "docs" / "lmcache_utils_rebuild.sh"
OUT_JSON = ROOT / "docs" / "fix-capability-results.json"
HOST_FILE = (
    "/home/m00926961/work/lmcache-ascend-scripts-master/LMCache-Ascend/csrc/utils.cpp"
)
SESSIONS = Path.home() / ".dsh" / "sessions"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = (
        str(Path.home() / ".local/node/bin")
        + ":"
        + str(Path.home() / "src/dsh-app/node_modules/.bin")
        + ":"
        + env.get("PATH", "")
    )
    env["PYTHONPATH"] = str(ROOT / "src")
    env["VLLM_API_KEY"] = "local"
    env["CHOKIDAR_USEPOLLING"] = "1"
    env["DSH_PERMISSION_MODE"] = "danger-full-access"
    return env


def _newest_session(cwd_slug: str, since: float) -> str | None:
    folder = SESSIONS / cwd_slug
    if not folder.is_dir():
        return None
    newest = None
    newest_mtime = since
    for child in folder.iterdir():
        if not child.name.startswith("session-"):
            continue
        mtime = child.stat().st_mtime
        if mtime >= newest_mtime:
            newest_mtime = mtime
            newest = child.name.replace("session-", "")
    return newest


def _load_session(cwd_slug: str, sid: str) -> list[dict[str, Any]]:
    path = SESSIONS / cwd_slug / f"session-{sid}" / "session.jsonl.zstd"
    raw = subprocess.check_output(["zstd", "-d", "-c", str(path)])
    events = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _summarize_session(events: list[dict[str, Any]]) -> dict[str, Any]:
    calls: dict[str, str] = {}
    n_diagrun = 0
    n_edit = 0
    n_bash = 0
    first_compact: dict[str, Any] | None = None
    last_compact: dict[str, Any] | None = None
    compact_bytes: list[int] = []

    def text_of(content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    inner = block.get("content")
                    if inner is not None and inner is not block:
                        nested = text_of(inner)
                        if nested:
                            parts.append(nested)
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(p for p in parts if p)
        if isinstance(content, dict):
            if content.get("type") == "text" and isinstance(content.get("text"), str):
                return content["text"]
            return text_of(content.get("content") or content.get("text") or [])
        return ""

    for ev in events:
        data = ev.get("data") or {}
        if ev.get("type") == "tool/call":
            cid = str(data.get("callId") or "")
            calls[cid] = str(data.get("name") or "")
            name = calls[cid]
            if name == "diagrun_build":
                n_diagrun += 1
            elif name in ("edit", "write"):
                n_edit += 1
            elif name == "bash":
                n_bash += 1
            continue
        if ev.get("type") != "tool/result":
            continue
        cid = str(((data.get("message") or {}).get("source") or {}).get("callId") or "")
        name = calls.get(cid, "")
        if name != "diagrun_build":
            continue
        blob = text_of((data.get("message") or {}).get("content"))
        compact_bytes.append(len(blob.encode()))
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            payload = {"raw_text": blob[:200]}
        if first_compact is None:
            first_compact = payload
        last_compact = payload

    return {
        "n_diagrun_build": n_diagrun,
        "n_edit": n_edit,
        "n_bash": n_bash,
        "compact_bytes": compact_bytes,
        "first_compact": first_compact,
        "last_compact": last_compact,
    }


def _rebuild() -> int:
    proc = subprocess.run(
        [str(REBUILD)],
        check=False,
        capture_output=True,
        text=True,
    )
    return int(proc.returncode)


def _prompt(case_id: str, title: str, propagation: str) -> str:
    return f"""Fix a C++ compile error in LMCache-Ascend.

Broken file (edit this host path):
  {HOST_FILE}

Error class: {case_id} — {title}
Propagation: {propagation}

1. Build with diagrun_build only. command:
   {REBUILD}
   cwd: this directory
   Do not use bash to compile.
2. Read the compact JSON (status, exit_code, roots, snippet). Call
   diagrun_get_raw only if roots and unclassified are both empty.
3. Edit {HOST_FILE} to fix the root cause. Keep the rest of the file.
4. Run diagrun_build again. Stop when exit_code is 0.

Reply with: first exit_code, what you changed, last exit_code.
"""


def run_case(case: dict[str, str]) -> dict[str, Any]:
    cwd = Path(f"/tmp/dsh-fix-{case['id']}")
    cwd.mkdir(parents=True, exist_ok=True)
    slug = cwd.name.replace("/", "-")
    # DSH session folder uses a slug of cwd; empirically /tmp/dsh-fix-X → --tmp-dsh-fix-X--
    cwd_slug = f"--{cwd.as_posix().strip('/').replace('/', '-')}--"
    inject(case)  # type: ignore[arg-type]
    t0 = time.time()
    proc = subprocess.run(
        ["dsh", "--profile", "headless", _prompt(case["id"], case["title"], case["propagation"])],
        cwd=str(cwd),
        env=_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - t0
    sid = _newest_session(cwd_slug, since=t0 - 5) or ""
    summary: dict[str, Any] = {}
    if sid:
        try:
            summary = _summarize_session(_load_session(cwd_slug, sid))
        except Exception as exc:  # noqa: BLE001
            summary = {"session_error": str(exc)}
    build_exit = _rebuild()
    last = summary.get("last_compact") or {}
    first = summary.get("first_compact") or {}
    return {
        "id": case["id"],
        "title": case["title"],
        "propagation": case["propagation"],
        "dsh_exit": proc.returncode,
        "dsh_stdout_tail": (proc.stdout or "")[-800:],
        "session_id": sid,
        "elapsed_s": round(elapsed, 1),
        "build_exit_after_agent": build_exit,
        "fixed": build_exit == 0,
        "first_status": first.get("status"),
        "first_exit_code": first.get("exit_code"),
        "first_raw_bytes": (first.get("raw") or {}).get("bytes"),
        "first_roots": first.get("roots"),
        "last_status": last.get("status"),
        "last_exit_code": last.get("exit_code"),
        "n_diagrun_build": summary.get("n_diagrun_build"),
        "n_edit": summary.get("n_edit"),
        "n_bash": summary.get("n_bash"),
        "compact_bytes": summary.get("compact_bytes"),
    }


def main() -> None:
    REBUILD.chmod(0o755)
    backup_vanilla()
    results: list[dict[str, Any]] = []
    try:
        for case in CASES:
            print(f"=== {case['id']} ===", flush=True)
            try:
                row = run_case(case)
            except Exception as exc:  # noqa: BLE001
                row = {"id": case["id"], "fixed": False, "error": str(exc)}
            results.append(row)
            print(json.dumps({k: row.get(k) for k in ("id", "fixed", "build_exit_after_agent", "session_id", "first_status", "last_status")}, ensure_ascii=False), flush=True)
            restore_vanilla()
    finally:
        restore_vanilla()
        print("vanilla restored", flush=True)
    OUT_JSON.write_text(json.dumps({"cases": results}, indent=2) + "\n")
    print(f"wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
