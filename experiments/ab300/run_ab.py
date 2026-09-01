#!/usr/bin/env python3
"""Checkpointed 300-case DSH A/B: dsh-diagrun bash wrap vs stock bash.

Always runs both arms with the same user prompt. Skips (case_id, arm)
already in results.jsonl. Writes a row only after the gold `make && ./app`
check.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CASES_DIR = HERE / "cases"
RESULTS = HERE / "results.jsonl"
LOGS = HERE / "logs"
SCRATCH = Path("/tmp/ab300")
GOLD = 42
DEFAULT_TIMEOUT_S = 1200

from generate_cases import GOLD_RETURN_CODE, generate  # noqa: E402
from measure_session import measure_cwd  # noqa: E402


def _env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = (
        str(Path.home() / ".local/node/bin")
        + ":"
        + str(Path.home() / "src/dsh-app/node_modules/.bin")
        + ":"
        + env.get("PATH", "")
    )
    env["PYTHONPATH"] = str(ROOT / "src") + (
        (":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else ""
    )
    env["VLLM_API_KEY"] = env.get("VLLM_API_KEY") or "local"
    env["CHOKIDAR_USEPOLLING"] = "1"
    env["DSH_PERMISSION_MODE"] = "danger-full-access"
    return env


def _done_keys(path: Path) -> Set[Tuple[str, str]]:
    keys: Set[Tuple[str, str]] = set()
    if not path.is_file():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = row.get("case_id")
        arm = row.get("arm")
        if cid and arm:
            keys.add((str(cid), str(arm)))
    return keys


def _append_row(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_index(cases_dir: Path) -> List[Dict[str, Any]]:
    index = cases_dir / "index.jsonl"
    if not index.is_file():
        raise FileNotFoundError(f"{index} missing; run generate_cases.py first")
    return [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]


def ensure_cases(cases_dir: Path) -> List[Dict[str, Any]]:
    index = cases_dir / "index.jsonl"
    if not index.is_file():
        print("generating 300 cases...", flush=True)
        generate(cases_dir)
    return load_index(cases_dir)


def ensure_no_diagrun_profile() -> None:
    """Clone headless without the dsh-diagrun bundle."""
    dst = Path.home() / ".dsh" / "profiles" / "headless-no-diagrun"
    src = Path.home() / ".dsh" / "profiles" / "headless"
    patch = (src / "cordis.patch.yml").read_text(encoding="utf-8") if (src / "cordis.patch.yml").is_file() else (
        "- id: agent-default-model\n"
        "  config:\n"
        "    provider: vllm\n"
        "    model: qwen3-8b\n"
        "- id: bash-sandbox\n"
        "  config:\n"
        "    timeoutMs: 600000\n"
    )
    dst.mkdir(parents=True, exist_ok=True)
    pkg = {
        "name": "dsh-profile-headless-no-diagrun",
        "private": True,
        "dsh": {
            "profile": {
                "bundles": [
                    "@deepseek-ai/dsh-base",
                    "@deepseek-ai/dsh-headless",
                ]
            }
        },
    }
    (dst / "package.json").write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    (dst / "pnpm-workspace.yaml").write_text(
        "packages:\n  - .\n\nnodeLinker: hoisted\nautoInstallPeers: false\n",
        encoding="utf-8",
    )
    (dst / "cordis.patch.yml").write_text(patch, encoding="utf-8")


def _prompt(_arm: str, fixture: Dict[str, Any], scratch: Path) -> str:
    gold = int(fixture.get("gold_return_code") or GOLD_RETURN_CODE)
    kind = fixture["kind"]
    return f"""Fix the C++ program in {scratch} so `make` succeeds and `./app` still exits with code {gold}.

Error class: {kind} — {fixture.get("propagation") or kind}
Injected file: {fixture.get("inject", {}).get("file")}

Rules:
- Make a MINIMAL edit that removes the injected fault. Do not delete main, do not empty the program, do not change ./app's return code, do not add a new API to paper over the error.
- Stop when the build exits 0.

1. Compile with the bash tool: `make` in {scratch}. Do not background it.
2. Inspect the compiler output, edit the injected fault, rebuild until make exits 0.

Reply with: what you changed, last make exit_code.
"""


def gold_check(scratch: Path, gold: int) -> Tuple[bool, int, Optional[int]]:
    """Return (fixed, make_exit, app_exit)."""
    make = subprocess.run(
        ["make", "-j8"],
        cwd=str(scratch),
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if make.returncode != 0:
        return False, int(make.returncode), None
    app = subprocess.run(
        [str(scratch / "app")],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return app.returncode == gold, 0, int(app.returncode)


def run_arm(
    fixture: Dict[str, Any],
    arm: str,
    *,
    timeout_s: int,
    cases_dir: Path,
) -> Dict[str, Any]:
    case_id = fixture["id"]
    gold = int(fixture.get("gold_return_code") or GOLD)
    src = cases_dir / case_id
    scratch = SCRATCH / case_id / arm
    if scratch.exists():
        shutil.rmtree(scratch)
    shutil.copytree(src, scratch)
    profile = "headless" if arm == "with" else "headless-no-diagrun"
    prompt = _prompt(arm, fixture, scratch)
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"{case_id}-{arm}.txt"
    t0 = time.time()
    error = None
    proc: Optional[subprocess.CompletedProcess[str]] = None
    try:
        proc = subprocess.run(
            ["dsh", "--profile", profile, prompt],
            cwd=str(scratch),
            env=_env(),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        error = f"dsh timeout after {timeout_s}s"
        stdout = (exc.stdout or b"") if isinstance(exc.stdout, (bytes, bytearray)) else (exc.stdout or "")
        stderr = (exc.stderr or b"") if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or "")
        if isinstance(stdout, (bytes, bytearray)):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, (bytes, bytearray)):
            stderr = stderr.decode("utf-8", "replace")
        log_path.write_text((stdout or "")[-80_000:] + "\n--- stderr ---\n" + (stderr or "")[-20_000:], encoding="utf-8")
        proc = None
    elapsed = round(time.time() - t0, 1)
    if proc is not None:
        log_path.write_text(
            (proc.stdout or "")[-80_000:] + "\n--- stderr ---\n" + (proc.stderr or "")[-20_000:],
            encoding="utf-8",
        )
    try:
        fixed, make_exit, app_exit = gold_check(scratch, gold)
    except Exception as exc:  # noqa: BLE001
        fixed, make_exit, app_exit = False, -1, None
        if error is None:
            error = f"gold check: {exc}"
    stats: Dict[str, Any] = {}
    try:
        stats = measure_cwd(scratch, since=t0 - 5)
    except Exception as exc:  # noqa: BLE001
        stats = {"session_error": str(exc)}
    row: Dict[str, Any] = {
        "case_id": case_id,
        "arm": arm,
        "kind": fixture["kind"],
        "size_class": fixture["size_class"],
        "n_tus": fixture["n_tus"],
        "session_id": stats.get("session_id") or "",
        "elapsed_s": elapsed,
        "dsh_exit": None if proc is None else proc.returncode,
        "fixed": fixed,
        "make_exit": make_exit,
        "app_exit": app_exit,
        "n_diagrun_build": stats.get("n_diagrun_build"),
        "n_bash": stats.get("n_bash"),
        "n_edit": stats.get("n_edit"),
        "n_read": stats.get("n_read"),
        "tool_result_bytes_total": stats.get("tool_result_bytes_total"),
        "tool_result_words_total": stats.get("tool_result_words_total"),
        "first_tool_result_bytes": stats.get("first_tool_result_bytes"),
        "last_tool_result_bytes": stats.get("last_tool_result_bytes"),
        "full_context_words": stats.get("full_context_words"),
    }
    if arm == "with":
        row["first_compact_status"] = stats.get("first_compact_status")
        row["first_raw_bytes"] = stats.get("first_raw_bytes")
        row["first_root_kinds"] = stats.get("first_root_kinds")
    else:
        row["spill"] = stats.get("spill")
        row["prune"] = stats.get("prune")
    if error or stats.get("error") or stats.get("session_error"):
        row["error"] = error or stats.get("error") or stats.get("session_error")
    return row


SMOKE_IDS = (
    "missing_member__1__v0",
    "syntax_cascade__verbose__v2",
)


def select_cases(
    fixtures: List[Dict[str, Any]],
    *,
    smoke: bool,
    limit: Optional[int],
    only: Optional[str],
) -> List[Dict[str, Any]]:
    if only:
        picked = [f for f in fixtures if f["id"] == only]
        if not picked:
            raise SystemExit(f"unknown case id {only}")
        return picked
    if smoke:
        by_id = {f["id"]: f for f in fixtures}
        missing = [cid for cid in SMOKE_IDS if cid not in by_id]
        if missing:
            raise SystemExit(f"smoke ids missing: {missing}")
        return [by_id[cid] for cid in SMOKE_IDS]
    if limit is not None:
        return fixtures[:limit]
    return fixtures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--smoke", action="store_true", help="2 cases × 2 arms")
    parser.add_argument("--limit", type=int, default=None, help="first N cases (both arms)")
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--arms", default="with,without")
    args = parser.parse_args()
    ensure_no_diagrun_profile()
    fixtures = ensure_cases(args.cases)
    selected = select_cases(fixtures, smoke=args.smoke, limit=args.limit, only=args.only)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    done = _done_keys(args.results)
    total = len(selected) * len(arms)
    n = 0
    print(
        f"{len(selected)} cases × {len(arms)} arms = {total} sessions; "
        f"{len(done)} already in {args.results}",
        flush=True,
    )
    for fixture in selected:
        for arm in arms:
            n += 1
            key = (fixture["id"], arm)
            if key in done:
                print(f"[{n}/{total}] skip {fixture['id']} {arm}", flush=True)
                continue
            print(f"[{n}/{total}] {fixture['id']} {arm} ...", flush=True)
            try:
                row = run_arm(fixture, arm, timeout_s=args.timeout, cases_dir=args.cases)
            except Exception as exc:  # noqa: BLE001
                row = {
                    "case_id": fixture["id"],
                    "arm": arm,
                    "kind": fixture["kind"],
                    "size_class": fixture["size_class"],
                    "n_tus": fixture["n_tus"],
                    "fixed": False,
                    "error": str(exc),
                }
            _append_row(args.results, row)
            done.add(key)
            print(
                json.dumps(
                    {
                        k: row.get(k)
                        for k in (
                            "case_id",
                            "arm",
                            "fixed",
                            "elapsed_s",
                            "session_id",
                            "full_context_words",
                            "error",
                        )
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    print(f"done. results: {args.results}", flush=True)


if __name__ == "__main__":
    main()
