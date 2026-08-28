"""Agent-facing tool operations shared by MCP, DeepSeek, and Pi."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from diagrun.config import DiagrunConfig
from diagrun.diagnostics.model import raw_ref
from diagrun.exec.runner import run_command
from diagrun.store.runs import RunNotFoundError, RunStore

CommandArg = Union[str, Sequence[str]]


def parse_command(command: CommandArg) -> list[str]:
    """Accept a shell string or an argv list."""
    if isinstance(command, str):
        argv = shlex.split(command)
        if not argv:
            raise ValueError("command must be non-empty")
        return argv
    argv = [str(part) for part in command]
    if not argv:
        raise ValueError("command must be non-empty")
    return argv


def _store(store_root: Optional[str] = None) -> RunStore:
    return RunStore(Path(store_root) if store_root else None)


def tool_build(
    command: CommandArg,
    *,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    store_root: Optional[str] = None,
    inject_diagnostics: Optional[bool] = None,
    collapse_parse_recovery: Optional[bool] = None,
) -> dict[str, Any]:
    """Run a build through diagrun and return compact agent JSON.

    Live compiler output is captured, not forwarded. Use ``tool_get_raw``
    for the full log. Parsed root diagnostics are included when present.
    """
    argv = parse_command(command)
    config = DiagrunConfig.resolve(
        inject_diagnostics=inject_diagnostics,
        collapse_parse_recovery=collapse_parse_recovery,
        env=env,
    )
    store = _store(store_root)
    captured = run_command(
        argv,
        store,
        cwd=cwd,
        env=env,
        passthrough=False,
        config=config,
    )
    meta = store.load_meta(captured.id)
    diag_path = store.run_dir(captured.id) / "diagnostics.json"
    diagnostics: Optional[dict[str, Any]] = None
    if diag_path.is_file():
        diagnostics = json.loads(diag_path.read_text(encoding="utf-8"))
    status = "passed" if captured.exit_code == 0 else "failed"
    result: dict[str, Any] = {
        "status": status,
        "command": " ".join(shlex.quote(part) for part in argv),
        "argv": list(argv),
        "exit_code": captured.exit_code,
        "cwd": captured.cwd,
        "run_id": captured.id,
        "raw": {
            "ref": raw_ref(captured.id),
            "bytes": captured.stdout_bytes + captured.stderr_bytes,
            "stdout_bytes": captured.stdout_bytes,
            "stderr_bytes": captured.stderr_bytes,
        },
        "inject": meta.get("inject"),
        "reducer": meta.get("reducer"),
    }
    if diagnostics is not None:
        result["diagnostics"] = diagnostics
    else:
        result["diagnostics"] = None
        result["hint"] = (
            "Full compiler output is at raw.ref; retrieve with diagrun_get_raw. "
            "Normalized root diagnostics appear here once GCC/Clang parsers run."
        )
    return result


def tool_get_raw(
    run_id: Optional[str] = None,
    *,
    offset: int = 0,
    limit: Optional[int] = None,
    store_root: Optional[str] = None,
) -> dict[str, Any]:
    """Return a byte/text slice of a stored raw log."""
    store = _store(store_root)
    resolved = run_id or store.last_id()
    if not resolved:
        raise RunNotFoundError("no runs stored")
    blob = store.raw_bytes(resolved)
    start = max(0, int(offset))
    end = len(blob) if limit is None else min(len(blob), start + max(0, int(limit)))
    chunk = blob[start:end]
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        text = chunk.decode("utf-8", errors="replace")
    return {
        "run_id": resolved,
        "ref": raw_ref(resolved),
        "offset": start,
        "limit": None if limit is None else int(limit),
        "total_bytes": len(blob),
        "returned_bytes": len(chunk),
        "truncated": end < len(blob) or start > 0,
        "text": text,
    }


def tool_show(
    run_id: Optional[str] = None,
    *,
    store_root: Optional[str] = None,
) -> dict[str, Any]:
    """Return stored run metadata."""
    store = _store(store_root)
    resolved = run_id or store.last_id()
    if not resolved:
        raise RunNotFoundError("no runs stored")
    meta = store.load_meta(resolved)
    diag_path = store.run_dir(resolved) / "diagnostics.json"
    if diag_path.is_file():
        meta["diagnostics"] = json.loads(diag_path.read_text(encoding="utf-8"))
    return meta


def tool_get_diagnostic(
    run_id: str,
    diagnostic_id: str,
    *,
    store_root: Optional[str] = None,
) -> dict[str, Any]:
    """Return one diagnostic from a run's diagnostics.json, if parsed."""
    store = _store(store_root)
    path = store.run_dir(run_id) / "diagnostics.json"
    if not path.is_file():
        return {
            "run_id": run_id,
            "id": diagnostic_id,
            "available": False,
            "hint": "No parsed diagnostics for this run yet. Use diagrun_get_raw.",
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    for group in payload.get("roots") or []:
        if group.get("id") == diagnostic_id:
            return {"run_id": run_id, "available": True, "diagnostic": group}
        for member in group.get("members") or []:
            if member.get("id") == diagnostic_id:
                return {"run_id": run_id, "available": True, "diagnostic": member}
    for item in payload.get("unclassified") or []:
        if item.get("id") == diagnostic_id:
            return {"run_id": run_id, "available": True, "diagnostic": item}
    return {
        "run_id": run_id,
        "id": diagnostic_id,
        "available": False,
        "hint": f"diagnostic {diagnostic_id!r} not found in this run",
    }


OPS = {
    "build": lambda params: tool_build(
        params["command"],
        cwd=params.get("cwd"),
        env=params.get("env"),
        store_root=params.get("store"),
        inject_diagnostics=params.get("inject_diagnostics"),
        collapse_parse_recovery=params.get("collapse_parse_recovery"),
    ),
    "get_raw": lambda params: tool_get_raw(
        params.get("run_id"),
        offset=int(params.get("offset") or 0),
        limit=params.get("limit"),
        store_root=params.get("store"),
    ),
    "show": lambda params: tool_show(
        params.get("run_id"),
        store_root=params.get("store"),
    ),
    "get_diagnostic": lambda params: tool_get_diagnostic(
        str(params["run_id"]),
        str(params["diagnostic_id"]),
        store_root=params.get("store"),
    ),
}


def dispatch(op: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Run one named tool operation."""
    if op not in OPS:
        raise ValueError(f"unknown op {op!r}; expected one of: {', '.join(OPS)}")
    return OPS[op](dict(params))


def call_main(argv: Sequence[str]) -> int:
    """CLI: ``diagrun call OP`` with JSON params on stdin, JSON result on stdout."""
    if not argv:
        sys.stderr.write("diagrun call: missing OP (build|get_raw|show|get_diagnostic)\n")
        return 2
    op = argv[0]
    raw = sys.stdin.read()
    params: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    try:
        result = dispatch(op, params)
    except RunNotFoundError as exc:
        json.dump({"ok": False, "error": "not_found", "message": str(exc)}, sys.stdout)
        sys.stdout.write("\n")
        return 2
    except (ValueError, KeyError, TypeError) as exc:
        json.dump({"ok": False, "error": "invalid_params", "message": str(exc)}, sys.stdout)
        sys.stdout.write("\n")
        return 2
    json.dump({"ok": True, "result": result}, sys.stdout)
    sys.stdout.write("\n")
    return 0
