#!/usr/bin/env python3
"""Measure model-visible DSH session context.

Uses the last tool result per callId (post-spill / post-pruner replacement),
plus system + schemas + user (+ runtime policy). Word count = Unicode \\w+.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SESSIONS = Path.home() / ".dsh" / "sessions"
WORD_RE = re.compile(r"\w+", re.UNICODE)
SPILL_MARKERS = ("output truncated", "[output truncated]", "spill")
PRUNE_MARKERS = ("tool result middle pruned", "middle pruned")


def words(text: str) -> int:
    """Count Unicode word tokens. Works for prose and JSON keys/values."""
    return len(WORD_RE.findall(text or ""))


def cwd_slug(cwd: Path) -> str:
    """DSH session folder slug for a working directory."""
    posix = cwd.resolve().as_posix().strip("/")
    return f"--{posix.replace('/', '-')}--"


def load_events(path: Path) -> List[dict]:
    """Load a zstd-compressed DSH session jsonl."""
    raw = subprocess.check_output(["zstd", "-d", "-c", str(path)])
    events: List[dict] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def message_text(content: object) -> str:
    """Pull visible text from a DSH message content tree."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                inner = block.get("content")
                if inner is not None and inner is not block:
                    nested = message_text(inner)
                    if nested:
                        parts.append(nested)
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            return content["text"]
        return message_text(content.get("content") or content.get("text") or [])
    return ""


def newest_session(cwd: Path, since: float) -> Tuple[str, Optional[Path]]:
    """Return (session_id, jsonl path) for the newest session of cwd after `since`."""
    slug = cwd_slug(cwd)
    folder = SESSIONS / slug
    if not folder.is_dir():
        # Fallback: search all session dirs that mention the leaf name.
        leaf = cwd.name
        newest_id = ""
        newest_path: Optional[Path] = None
        newest_mtime = since
        if SESSIONS.is_dir():
            for child in SESSIONS.iterdir():
                if leaf not in child.name:
                    continue
                for sess in child.iterdir():
                    if not sess.name.startswith("session-"):
                        continue
                    mtime = sess.stat().st_mtime
                    if mtime >= newest_mtime:
                        newest_mtime = mtime
                        newest_id = sess.name.replace("session-", "")
                        newest_path = sess / "session.jsonl.zstd"
        return newest_id, newest_path
    newest_id = ""
    newest_path = None
    newest_mtime = since
    for sess in folder.iterdir():
        if not sess.name.startswith("session-"):
            continue
        mtime = sess.stat().st_mtime
        if mtime >= newest_mtime:
            newest_mtime = mtime
            newest_id = sess.name.replace("session-", "")
            newest_path = sess / "session.jsonl.zstd"
    return newest_id, newest_path


def _last_results(events: Iterable[dict]) -> Tuple[Dict[str, str], List[Tuple[str, str, str]]]:
    """Return callId→name and ordered last (callId, name, text) pairs."""
    calls: Dict[str, str] = {}
    order: List[str] = []
    latest: Dict[str, Tuple[str, str]] = {}
    for ev in events:
        data = ev.get("data") or {}
        if ev.get("type") == "tool/call":
            cid = str(data.get("callId") or "")
            if cid:
                calls[cid] = str(data.get("name") or "")
            continue
        if ev.get("type") != "tool/result":
            continue
        src = (data.get("message") or {}).get("source") or {}
        cid = str(src.get("callId") or data.get("callId") or "")
        name = calls.get(cid, "")
        text = message_text((data.get("message") or {}).get("content"))
        if cid and cid not in latest:
            order.append(cid)
        if cid:
            latest[cid] = (name, text)
    ordered = [(cid, latest[cid][0], latest[cid][1]) for cid in order if cid in latest]
    return calls, ordered


def is_compact_payload(payload: Optional[dict]) -> bool:
    """True when a tool result is a diagrun agent payload (roots JSON)."""
    if not payload:
        return False
    return "status" in payload and "exit_code" in payload and (
        "roots" in payload or "run_id" in payload
    )


def _parse_json_blob(text: str) -> Optional[dict]:
    blob = text.strip()
    if not blob:
        return None
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        start = blob.find("{")
        end = blob.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(blob[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def classify_result(name: str, text: str) -> str:
    """Bucket a tool result: compact roots JSON, compiler dump, or other."""
    n = (name or "").lower()
    if n in ("diagrun_build", "bash") and is_compact_payload(_parse_json_blob(text)):
        return "compact"
    if n == "bash":
        return "dump"
    if n in ("edit", "write"):
        return "edit"
    if n == "read":
        return "read"
    return "other"


def split_tool_words(events: List[dict]) -> Dict[str, Any]:
    """Word counts by tool-result class (last payload per callId)."""
    _calls, ordered = _last_results(events)
    buckets = {"compact": 0, "dump": 0, "edit": 0, "read": 0, "other": 0}
    first_build_kind: Optional[str] = None
    first_build_words = 0
    first_build_bytes = 0
    n_compact = 0
    n_dump = 0
    for _cid, name, text in ordered:
        kind = classify_result(name, text)
        w = words(text)
        buckets[kind] = buckets.get(kind, 0) + w
        if kind == "compact":
            n_compact += 1
        elif kind == "dump":
            n_dump += 1
        if first_build_kind is None and kind in ("compact", "dump"):
            first_build_kind = kind
            first_build_words = w
            first_build_bytes = len(text.encode("utf-8"))
    return {
        "compact_words": buckets["compact"],
        "dump_words": buckets["dump"],
        "edit_words": buckets["edit"],
        "read_words": buckets["read"],
        "other_tool_words": buckets["other"],
        "build_words": buckets["compact"] + buckets["dump"],
        "n_compact": n_compact,
        "n_dump": n_dump,
        "first_build_kind": first_build_kind,
        "first_build_words": first_build_words,
        "first_build_bytes": first_build_bytes,
    }


def measure_events(events: List[dict]) -> Dict[str, Any]:
    """Compute context and call-count stats from a loaded session."""
    system_text = ""
    tools: list = []
    for ev in events:
        if ev.get("type") != "request/header":
            continue
        header = (ev.get("data") or {}).get("header") or {}
        system_text = header.get("system") or ""
        tools = header.get("tools") or []
        break
    schema_text = json.dumps(tools, ensure_ascii=False)
    user_text = ""
    policy_text = ""
    saw_task = False
    saw_policy = False
    for ev in events:
        if ev.get("type") != "user/message":
            continue
        data = ev.get("data") or {}
        text = message_text(data.get("content"))
        if not text.strip():
            continue
        source = data.get("source") or {}
        kind = source.get("kind") or ""
        if not saw_task and (kind == "user" or "fails to compile" in text.lower() or "Fix the C++" in text):
            user_text = text
            saw_task = True
            continue
        if (not saw_policy) and (source.get("form") == "snapshot" or "runtime context" in text.lower()):
            policy_text = text
            saw_policy = True

    _calls, ordered = _last_results(events)
    n_diagrun = 0
    n_bash = 0
    n_edit = 0
    n_read = 0
    n_write = 0
    for _cid, name, text in ordered:
        if name == "diagrun_build":
            n_diagrun += 1
        elif name == "bash":
            n_bash += 1
            if is_compact_payload(_parse_json_blob(text)):
                n_diagrun += 1
        elif name == "edit":
            n_edit += 1
        elif name == "write":
            n_write += 1
        elif name == "read":
            n_read += 1

    result_bytes = [len(text.encode("utf-8")) for _cid, _n, text in ordered]
    result_words = [words(text) for _cid, _n, text in ordered]
    spill = False
    prune = False
    for _cid, name, text in ordered:
        lower = text.lower()
        if name == "bash" and not is_compact_payload(_parse_json_blob(text)):
            if any(m in lower for m in SPILL_MARKERS):
                spill = True
            if any(m in lower for m in PRUNE_MARKERS):
                prune = True

    first_compact: Optional[dict] = None
    first_root_kinds: List[str] = []
    first_raw_bytes: Optional[int] = None
    first_compact_status: Optional[str] = None
    for _cid, name, text in ordered:
        if name not in ("diagrun_build", "bash"):
            continue
        payload = _parse_json_blob(text)
        if not is_compact_payload(payload):
            continue
        first_compact = payload
        first_compact_status = payload.get("status")
        raw = (payload or {}).get("raw") or {}
        if isinstance(raw, dict):
            first_raw_bytes = raw.get("bytes")
        roots = (payload or {}).get("roots") or []
        if isinstance(roots, list):
            first_root_kinds = [
                str(r.get("kind") or "")
                for r in roots
                if isinstance(r, dict)
            ]
        break

    prefix_words = words(system_text) + words(schema_text) + words(user_text) + words(policy_text)
    full_context_words = prefix_words + sum(result_words)
    return {
        "n_diagrun_build": n_diagrun,
        "n_bash": n_bash,
        "n_edit": n_edit + n_write,
        "n_read": n_read,
        "n_write": n_write,
        "n_tool_results": len(ordered),
        "tool_result_bytes_total": sum(result_bytes),
        "tool_result_words_total": sum(result_words),
        "first_tool_result_bytes": result_bytes[0] if result_bytes else 0,
        "last_tool_result_bytes": result_bytes[-1] if result_bytes else 0,
        "full_context_words": full_context_words,
        "prefix_words": prefix_words,
        "spill": spill,
        "prune": prune,
        "first_compact_status": first_compact_status,
        "first_raw_bytes": first_raw_bytes,
        "first_root_kinds": first_root_kinds,
        "first_compact": first_compact,
        "n_tools_in_schema": len(tools),
    }


def measure_path(path: Path) -> Dict[str, Any]:
    """Measure a session.jsonl.zstd file."""
    return measure_events(load_events(path))


def measure_cwd(cwd: Path, since: float) -> Dict[str, Any]:
    """Find the newest session for cwd and measure it."""
    sid, path = newest_session(cwd, since)
    if not sid or path is None or not path.is_file():
        return {"session_id": sid, "error": "session not found"}
    stats = measure_path(path)
    stats["session_id"] = sid
    stats["session_path"] = str(path)
    return stats
