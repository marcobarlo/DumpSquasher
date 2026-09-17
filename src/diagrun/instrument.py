"""Always-on log of compiler dumps vs compact agent summaries.

Used to improve the reducer: each build writes the full compiler transcript
and the JSON actually returned to the agent. Lives under ``<store>/instrument``
so run-store GC does not delete it.

Never writes to the agent-facing stdout/stderr. Never raises to callers.
Disable with ``DIAGRUN_INSTRUMENT=0``.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from diagrun.config import env_bool
from diagrun.exec.runner import CapturedRun
from diagrun.render.json import dumps_compact
from diagrun.store.runs import RunStore

_WORDS = re.compile(r"\w+", re.UNICODE)


def enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Instrumentation is on unless ``DIAGRUN_INSTRUMENT`` is false."""
    return env_bool("DIAGRUN_INSTRUMENT", True, environ)


def instrument_root(store: RunStore, environ: Optional[Mapping[str, str]] = None) -> Path:
    """Directory for paired compiler/agent logs."""
    source = os.environ if environ is None else environ
    override = source.get("DIAGRUN_INSTRUMENT_DIR")
    if override and override.strip():
        return Path(override)
    return store.root / "instrument"


def record_build(
    store: RunStore,
    captured: CapturedRun,
    payload: Mapping[str, Any],
    *,
    surface: str,
    returned_to_agent: bool,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    """Persist compiler output and the agent summary for one build.

    Returns the per-run instrument directory, or None if disabled/failed.
    """
    if not enabled(environ):
        return None
    try:
        return _record_build(
            store,
            captured,
            payload,
            surface=surface,
            returned_to_agent=returned_to_agent,
            environ=environ,
        )
    except Exception:
        return None


def _record_build(
    store: RunStore,
    captured: CapturedRun,
    payload: Mapping[str, Any],
    *,
    surface: str,
    returned_to_agent: bool,
    environ: Optional[Mapping[str, str]],
) -> Path:
    root = instrument_root(store, environ)
    run_dir = root / captured.id
    run_dir.mkdir(parents=True, exist_ok=True)

    compiler = store.raw_bytes(captured.id)
    try:
        compiler_text = compiler.decode("utf-8")
    except UnicodeDecodeError:
        compiler_text = compiler.decode("utf-8", errors="replace")
    (run_dir / "compiler.log").write_bytes(compiler)

    agent_text = dumps_compact(payload)
    (run_dir / "agent.json").write_text(agent_text + "\n", encoding="utf-8")

    stdout = store.stream_bytes(captured.id, "stdout")
    stderr = store.stream_bytes(captured.id, "stderr")
    (run_dir / "stdout.bin").write_bytes(stdout)
    (run_dir / "stderr.bin").write_bytes(stderr)

    compiler_bytes = len(compiler)
    agent_bytes = len(agent_text.encode("utf-8"))
    compiler_words = len(_WORDS.findall(compiler_text))
    agent_words = len(_WORDS.findall(agent_text))
    ratio = (agent_bytes / compiler_bytes) if compiler_bytes else None
    record = {
        "ts_ns": time.time_ns(),
        "run_id": captured.id,
        "surface": surface,
        "returned_to_agent": returned_to_agent,
        "command": list(captured.command),
        "cwd": captured.cwd,
        "exit_code": captured.exit_code,
        "compiler_bytes": compiler_bytes,
        "compiler_words": compiler_words,
        "agent_bytes": agent_bytes,
        "agent_words": agent_words,
        "ratio_bytes": ratio,
        "stdout_bytes": captured.stdout_bytes,
        "stderr_bytes": captured.stderr_bytes,
        "roots": len(payload.get("roots") or []),
        "unclassified": len(payload.get("unclassified") or []),
        "status": payload.get("status"),
        "paths": {
            "compiler": str(run_dir / "compiler.log"),
            "agent": str(run_dir / "agent.json"),
        },
    }
    (run_dir / "record.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _append_line(root / "journal.jsonl", json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    ratio_s = f"{ratio:.4f}" if isinstance(ratio, float) else "n/a"
    _append_line(
        root / "diagrun-instrument.log",
        "run_id={run_id} surface={surface} returned={returned} exit={exit} "
        "compiler_bytes={cbytes} agent_bytes={abytes} ratio={ratio} "
        "roots={roots} unclassified={unclass} compiler={compiler} agent={agent}".format(
            run_id=record["run_id"],
            surface=record["surface"],
            returned=record["returned_to_agent"],
            exit=record["exit_code"],
            cbytes=record["compiler_bytes"],
            abytes=record["agent_bytes"],
            ratio=ratio_s,
            roots=record["roots"],
            unclass=record["unclassified"],
            compiler=record["paths"]["compiler"],
            agent=record["paths"]["agent"],
        ),
    )
    return run_dir


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        handle.write(line + "\n")
        handle.flush()
