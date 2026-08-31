"""Command-line interface: wrap a build, or retrieve a stored run."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from diagrun.config import DiagrunConfig
from diagrun.exec.runner import run_command
from diagrun.reducer.pipeline import persist_diagnostics
from diagrun.store.runs import RunNotFoundError, RunStore

USAGE = """\
usage: diagrun [options] [--] COMMAND [ARGS...]
       diagrun [options] show [--raw] RUN_ID
       diagrun [options] show [--raw] --last
       diagrun mcp
       diagrun call OP

Wrap a build command, capture stdout/stderr independently, and store the
raw log under a ULID run id. On success or failure the child's exit code
is returned unchanged. Streams are passed through live.

Agent integrations:
  diagrun mcp          MCP stdio server (DeepSeek / Pi / any MCP client)
  diagrun call OP      JSON-in/JSON-out tool call (stdin params)

By default, codegen-neutral diagnostic flags are injected

By default, codegen-neutral diagnostic flags are injected
(-fdiagnostics-format=json) via compiler argv, Make CC/CXX wrappers,
CMake COMPILER_LAUNCHER, Ninja PATH shims, and Clang CCC_OVERRIDE_OPTIONS.

Options:
  --store DIR
  --max-runs N
  --max-bytes N
  --inject-diagnostics       inject structured diagnostic flags (default)
  --no-inject-diagnostics    parse the log as the build emitted it
  --collapse-parse-recovery  hide likely syntax-recovery follow-on errors
  --no-collapse-parse-recovery
                             keep uncertain follow-on errors (default)

Retrieve a run:
  diagrun show RUN_ID
  diagrun show RUN_ID --raw
  diagrun show --last --raw
"""


@dataclass
class _GlobalArgs:
    store_root: Optional[Path] = None
    max_runs: Optional[int] = None
    max_bytes: Optional[int] = None
    inject_diagnostics: Optional[bool] = None
    collapse_parse_recovery: Optional[bool] = None
    rest: Optional[list[str]] = None


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return 0
    if args and args[0] == "--version":
        from diagrun import __version__

        sys.stdout.write(__version__ + "\n")
        return 0

    parsed = _parse_global(args)
    if parsed.rest is None:
        sys.stderr.write(USAGE)
        return 2
    if not parsed.rest:
        sys.stderr.write(USAGE)
        return 2

    store = RunStore(parsed.store_root, max_runs=parsed.max_runs, max_bytes=parsed.max_bytes)
    if parsed.rest[0] == "show":
        return _cmd_show(store, parsed.rest[1:])
    if parsed.rest[0] == "mcp":
        from diagrun.integrations.mcp_server import serve

        return serve()
    if parsed.rest[0] == "call":
        from diagrun.integrations.api import call_main

        return call_main(parsed.rest[1:])
    config = DiagrunConfig.resolve(
        inject_diagnostics=parsed.inject_diagnostics,
        collapse_parse_recovery=parsed.collapse_parse_recovery,
    )
    captured = run_command(parsed.rest, store, passthrough=True, config=config)
    persist_diagnostics(store, captured)
    return captured.exit_code


def _parse_global(argv: Sequence[str]) -> _GlobalArgs:
    parsed = _GlobalArgs()
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--":
            parsed.rest = list(argv[index + 1 :])
            return parsed
        if item == "--store":
            if index + 1 >= len(argv):
                sys.stderr.write("diagrun: --store requires a path\n")
                return parsed
            parsed.store_root = Path(argv[index + 1])
            index += 2
            continue
        if item.startswith("--store="):
            parsed.store_root = Path(item.split("=", 1)[1])
            index += 1
            continue
        if item == "--max-runs":
            if index + 1 >= len(argv):
                sys.stderr.write("diagrun: --max-runs requires an integer\n")
                return parsed
            parsed.max_runs = int(argv[index + 1])
            index += 2
            continue
        if item == "--max-bytes":
            if index + 1 >= len(argv):
                sys.stderr.write("diagrun: --max-bytes requires an integer\n")
                return parsed
            parsed.max_bytes = int(argv[index + 1])
            index += 2
            continue
        if item in ("--inject-diagnostics", "--inject"):
            parsed.inject_diagnostics = True
            index += 1
            continue
        if item in ("--no-inject-diagnostics", "--no-inject"):
            parsed.inject_diagnostics = False
            index += 1
            continue
        if item == "--collapse-parse-recovery":
            parsed.collapse_parse_recovery = True
            index += 1
            continue
        if item == "--no-collapse-parse-recovery":
            parsed.collapse_parse_recovery = False
            index += 1
            continue
        if item.startswith("-"):
            sys.stderr.write(f"diagrun: unknown option {item}\n")
            return parsed
        parsed.rest = list(argv[index:])
        return parsed
    parsed.rest = []
    return parsed


def _cmd_show(store: RunStore, args: Sequence[str]) -> int:
    raw = False
    last = False
    run_id: Optional[str] = None
    for item in args:
        if item == "--raw":
            raw = True
        elif item == "--last":
            last = True
        elif item.startswith("-"):
            sys.stderr.write(f"diagrun: unknown option {item}\n")
            return 2
        elif run_id is None:
            run_id = item
        else:
            sys.stderr.write("diagrun: extra argument for show\n")
            return 2
    if last and run_id:
        sys.stderr.write("diagrun: pass either RUN_ID or --last, not both\n")
        return 2
    if last:
        run_id = store.last_id()
        if not run_id:
            sys.stderr.write("diagrun: no runs stored\n")
            return 2
    if not run_id:
        sys.stderr.write("diagrun: show requires RUN_ID or --last\n")
        return 2
    try:
        if raw:
            sys.stdout.buffer.write(store.raw_bytes(run_id))
            return 0
        meta = store.load_meta(run_id)
        json.dump(meta, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    except RunNotFoundError:
        sys.stderr.write(f"diagrun: run not found: {run_id}\n")
        return 2
    except ValueError as exc:
        sys.stderr.write(f"diagrun: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
