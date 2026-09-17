"""Command-line interface: wrap a build, or retrieve a stored run."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from diagrun.config import DiagrunConfig
from diagrun.exec.runner import run_command
from diagrun.instrument import record_build
from diagrun.reducer.pipeline import check_progress, persist_diagnostics
from diagrun.render.json import agent_payload, dumps_compact
from diagrun.store.runs import RunNotFoundError, RunStore

USAGE = """\
usage: diagrun [options] [--] COMMAND [ARGS...]
       diagrun [options] show [--raw] RUN_ID
       diagrun [options] show [--raw] --last
       diagrun mcp
       diagrun call OP
       diagrun install-wrappers [--dir DIR]

Wrap a build command, capture stdout/stderr independently, and store the
raw log under a ULID run id. On success or failure the child's exit code
is returned unchanged. Streams are passed through live unless --format json.

Agent integrations:
  diagrun mcp          MCP stdio server (DeepSeek / Pi / any MCP client)
  diagrun call OP      JSON-in/JSON-out tool call (stdin params)
  diagrun --format json -- cmake --build DIR
                       compact roots JSON, original exit code (pip/setup.py)

By default, codegen-neutral diagnostic flags are injected
(-fdiagnostics-format=json) via compiler argv, Make CC/CXX wrappers,
CMake COMPILER_LAUNCHER, Ninja PATH shims, and Clang CCC_OVERRIDE_OPTIONS.

Options:
  --store DIR
  --max-runs N
  --max-bytes N
  --format json|passthrough  json: compact agent payload, no live dump
  --inject-diagnostics       inject structured diagnostic flags (default)
  --no-inject-diagnostics    parse the log as the build emitted it
  --collapse-parse-recovery  hide likely syntax-recovery follow-on errors
  --no-collapse-parse-recovery
                             keep uncertain follow-on errors (default)

Retrieve a run:
  diagrun show RUN_ID
  diagrun show RUN_ID --raw
  diagrun show --last --raw

PATH shims (cmake --build → diagrun --format json):
  diagrun install-wrappers --dir /opt/diagrun/bin
  export PATH="/opt/diagrun/bin:$PATH"

Each build also writes ``<store>/instrument/<run_id>/{compiler.log,agent.json}``
and appends ``journal.jsonl``. Disable with DIAGRUN_INSTRUMENT=0.
"""


@dataclass
class _GlobalArgs:
    store_root: Optional[Path] = None
    max_runs: Optional[int] = None
    max_bytes: Optional[int] = None
    inject_diagnostics: Optional[bool] = None
    collapse_parse_recovery: Optional[bool] = None
    output_format: str = "passthrough"
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
    if parsed.rest[0] == "install-wrappers":
        return _cmd_install_wrappers(parsed.rest[1:])

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
    passthrough = parsed.output_format != "json"
    captured = run_command(parsed.rest, store, passthrough=passthrough, config=config)
    reduced = persist_diagnostics(store, captured)
    if parsed.output_format == "json":
        progressed = check_progress(store, captured.cwd, reduced)
        payload = agent_payload(reduced, captured.id, no_progress=not progressed)
        record_build(
            store,
            captured,
            payload,
            surface="cli_json",
            returned_to_agent=True,
        )
        sys.stdout.write(dumps_compact(payload) + "\n")
    else:
        payload = agent_payload(reduced, captured.id)
        record_build(
            store,
            captured,
            payload,
            surface="cli",
            returned_to_agent=False,
        )
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
        if item in ("--format", "-F"):
            if index + 1 >= len(argv):
                sys.stderr.write("diagrun: --format requires json or passthrough\n")
                return parsed
            parsed.output_format = argv[index + 1].strip().lower()
            if parsed.output_format not in ("json", "passthrough"):
                sys.stderr.write("diagrun: --format must be json or passthrough\n")
                return parsed
            index += 2
            continue
        if item.startswith("--format="):
            parsed.output_format = item.split("=", 1)[1].strip().lower()
            if parsed.output_format not in ("json", "passthrough"):
                sys.stderr.write("diagrun: --format must be json or passthrough\n")
                return parsed
            index += 1
            continue
        if item in ("--compact", "--json"):
            parsed.output_format = "json"
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


def _cmd_install_wrappers(args: Sequence[str]) -> int:
    from diagrun.exec.pathwrap import default_wrapper_dir, install_wrappers

    directory: Optional[Path] = None
    index = 0
    while index < len(args):
        item = args[index]
        if item in ("--dir", "-d"):
            if index + 1 >= len(args):
                sys.stderr.write("diagrun: --dir requires a path\n")
                return 2
            directory = Path(args[index + 1])
            index += 2
            continue
        if item.startswith("--dir="):
            directory = Path(item.split("=", 1)[1])
            index += 1
            continue
        sys.stderr.write(f"diagrun: unknown install-wrappers option {item}\n")
        return 2
    target = directory or default_wrapper_dir()
    try:
        written = install_wrappers(target)
    except FileNotFoundError as exc:
        sys.stderr.write(f"diagrun: {exc}\n")
        return 2
    sys.stdout.write(f"wrote {len(written)} shim(s) in {target}\n")
    for path in written:
        sys.stdout.write(f"  {path}\n")
    sys.stdout.write(f"export PATH=\"{target}:$PATH\"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
