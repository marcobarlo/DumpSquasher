"""MCP stdio server for diagrun agent tools."""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, Optional

from diagrun import __version__
from diagrun.integrations.api import dispatch
from diagrun.store.runs import RunNotFoundError

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "diagrun_build",
        "description": (
            "Run a C++ build (cmake/ninja/make/g++) through diagrun. "
            "Returns compact root diagnostics the model can act on. "
            "Do not call diagrun_get_raw unless roots and unclassified are both empty. "
            "If the result has \"no_progress\": true, your last edit did not change the "
            "roots — stop rebuilding and re-inspect the diff instead of calling this again."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "description": "Build command as a shell string or argv array.",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "cwd": {"type": "string", "description": "Working directory."},
                "inject_diagnostics": {
                    "type": "boolean",
                    "description": "Inject -fdiagnostics-format=json (default true).",
                },
                "collapse_parse_recovery": {
                    "type": "boolean",
                    "description": (
                        "Fold syntax-recovery follow-on errors into the causing root's "
                        "evidence instead of listing them as separate roots (default true)."
                    ),
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "diagrun_get_raw",
        "description": (
            "Retrieve stored raw build output. Call only when diagrun_build returned "
            "empty roots and empty unclassified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run id; omit for last run."},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 0},
            },
        },
    },
    {
        "name": "diagrun_show",
        "description": "Return metadata for a stored diagrun run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run id; omit for last run."},
            },
        },
    },
    {
        "name": "diagrun_get_diagnostic",
        "description": "Return one parsed diagnostic from a run, when parsers have produced diagnostics.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "diagnostic_id": {"type": "string"},
            },
            "required": ["run_id", "diagnostic_id"],
        },
    },
]

_OP = {
    "diagrun_build": "build",
    "diagrun_get_raw": "get_raw",
    "diagrun_show": "show",
    "diagrun_get_diagnostic": "get_diagnostic",
}


def serve(
    stdin: Optional[BinaryIO] = None,
    stdout: Optional[BinaryIO] = None,
) -> int:
    """Serve MCP JSON-RPC on stdio until EOF."""
    incoming = stdin if stdin is not None else sys.stdin.buffer
    outgoing = stdout if stdout is not None else sys.stdout.buffer
    while True:
        message = _read_message(incoming)
        if message is None:
            return 0
        if message.get("method") and message.get("id") is None:
            _handle_notification(message)
            continue
        response = _handle_request(message)
        if response is not None:
            _write_message(outgoing, response)
    return 0


def _handle_notification(message: dict[str, Any]) -> None:
    return None


def _handle_request(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}
    try:
        if method == "initialize":
            result: Any = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "diagrun", "version": __version__},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = _call_tool(params)
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except Exception as exc:  # noqa: BLE001 — surface to MCP client
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": str(exc)},
        }


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    op = _OP.get(name)
    if op is None:
        return {
            "content": [{"type": "text", "text": f"unknown tool {name}"}],
            "isError": True,
        }
    try:
        value = dispatch(op, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}],
            "isError": False,
            "structuredContent": value,
        }
    except RunNotFoundError as exc:
        return {
            "content": [{"type": "text", "text": f"run not found: {exc}"}],
            "isError": True,
        }
    except (ValueError, KeyError, TypeError) as exc:
        return {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
        }


def _read_message(incoming: BinaryIO) -> Optional[dict[str, Any]]:
    header = b""
    while b"\r\n\r\n" not in header and b"\n\n" not in header:
        chunk = incoming.read(1)
        if not chunk:
            return None if not header else _parse_json(header)
        header += chunk
        if header.startswith(b"{") or header.startswith(b"["):
            rest = incoming.readline()
            return _parse_json(header + rest)
    if b"\r\n\r\n" in header:
        raw_headers, remainder = header.split(b"\r\n\r\n", 1)
    else:
        raw_headers, remainder = header.split(b"\n\n", 1)
    length = 0
    for line in raw_headers.replace(b"\r\n", b"\n").split(b"\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    body = remainder
    while len(body) < length:
        chunk = incoming.read(length - len(body))
        if not chunk:
            break
        body += chunk
    return _parse_json(body[:length] if length else body)


def _parse_json(blob: bytes) -> dict[str, Any]:
    text = blob.decode("utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("MCP message must be a JSON object")
    return data


def _write_message(outgoing: BinaryIO, message: dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    outgoing.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
    outgoing.flush()
