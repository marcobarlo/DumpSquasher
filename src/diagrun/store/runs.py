"""Local raw-output store with retention limits."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from diagrun.exec.capture import load_events, reconstruct_raw
from diagrun.ids import new_ulid

if TYPE_CHECKING:
    from diagrun.exec.runner import CapturedRun

DEFAULT_MAX_RUNS = 100
DEFAULT_MAX_BYTES = 512 * 1024 * 1024


def default_store_path() -> Path:
    """Return ``$DIAGRUN_STORE`` or the XDG data directory."""
    override = os.environ.get("DIAGRUN_STORE")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "diagrun"
    return Path.home() / ".local" / "share" / "diagrun"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


class RunNotFoundError(KeyError):
    """Raised when a run id is missing from the store."""


class RunStore:
    """Filesystem store: ``<root>/runs/<id>/`` plus a ``last`` pointer."""

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        max_runs: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ) -> None:
        self.root = Path(root) if root is not None else default_store_path()
        self.runs_dir = self.root / "runs"
        if max_runs is None:
            self.max_runs = _env_int("DIAGRUN_MAX_RUNS", DEFAULT_MAX_RUNS)
        else:
            self.max_runs = max_runs
        if max_bytes is None:
            self.max_bytes = _env_int("DIAGRUN_MAX_BYTES", DEFAULT_MAX_BYTES)
        else:
            self.max_bytes = max_bytes
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def allocate(self) -> tuple[str, Path]:
        """Create a unique run directory and return ``(run_id, path)``."""
        for _ in range(8):
            run_id = new_ulid()
            path = self.runs_dir / run_id
            try:
                path.mkdir(parents=True, exist_ok=False)
                return run_id, path
            except FileExistsError:
                continue
        raise RuntimeError("could not allocate a unique run id")

    def run_dir(self, run_id: str) -> Path:
        if "/" in run_id or "\\" in run_id or run_id in (".", ".."):
            raise ValueError(f"invalid run id {run_id!r}")
        path = (self.runs_dir / run_id).resolve()
        if not str(path).startswith(str(self.runs_dir.resolve())):
            raise ValueError(f"invalid run id {run_id!r}")
        return path

    def finalize(self, captured: CapturedRun) -> None:
        """Write meta.json, update the last-run pointer, then apply retention."""
        path = self.run_dir(captured.id)
        meta = {
            "id": captured.id,
            "command": list(captured.command),
            "cwd": captured.cwd,
            "exit_code": captured.exit_code,
            "started_at_ns": captured.started_at_ns,
            "finished_at_ns": captured.finished_at_ns,
            "stdout_bytes": captured.stdout_bytes,
            "stderr_bytes": captured.stderr_bytes,
            "event_count": captured.event_count,
            "raw": {"ref": f"diag://run/{captured.id}/raw"},
            "inject": {
                "enabled": captured.inject_enabled,
                "methods": list(captured.inject_methods),
                "flags": list(captured.inject_flags),
            },
            "reducer": {
                "collapse_parse_recovery": captured.collapse_parse_recovery,
            },
            "executed": list(captured.executed),
        }
        meta_path = path / "meta.json"
        tmp = path / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        tmp.replace(meta_path)
        self._set_last(captured.id)
        self.gc(keep=captured.id)

    def last_id(self) -> Optional[str]:
        pointer = self.root / "last"
        if not pointer.is_file():
            return None
        value = pointer.read_text(encoding="utf-8").strip()
        return value or None

    def load_meta(self, run_id: str) -> dict[str, object]:
        path = self.run_dir(run_id) / "meta.json"
        if not path.is_file():
            raise RunNotFoundError(run_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def raw_bytes(self, run_id: str) -> bytes:
        """Return interleaved raw output for a stored run."""
        path = self.run_dir(run_id)
        if not (path / "meta.json").is_file():
            raise RunNotFoundError(run_id)
        stdout = (path / "stdout.bin").read_bytes() if (path / "stdout.bin").is_file() else b""
        stderr = (path / "stderr.bin").read_bytes() if (path / "stderr.bin").is_file() else b""
        events = load_events(path / "events.jsonl")
        return reconstruct_raw(stdout, stderr, events)

    def stream_bytes(self, run_id: str, stream: str) -> bytes:
        path = self.run_dir(run_id)
        if not (path / "meta.json").is_file():
            raise RunNotFoundError(run_id)
        blob = path / f"{stream}.bin"
        return blob.read_bytes() if blob.is_file() else b""

    def gc(self, keep: Optional[str] = None) -> None:
        """Delete oldest runs until count and byte limits hold.

        A single run larger than ``max_bytes`` is kept if it is ``keep``.
        Limits <= 0 disable that dimension.
        """
        runs = [path for path in self.runs_dir.iterdir() if path.is_dir()]
        runs.sort(key=lambda path: path.name)
        while True:
            if keep is not None:
                remaining = [path for path in runs if path.name != keep]
            else:
                remaining = list(runs)
            over_count = self.max_runs > 0 and len(runs) > self.max_runs
            total = sum(_dir_size(path) for path in runs)
            over_bytes = self.max_bytes > 0 and total > self.max_bytes
            if not over_count and not over_bytes:
                return
            if not remaining:
                return
            victim = remaining[0]
            shutil.rmtree(victim, ignore_errors=True)
            runs = [path for path in runs if path != victim]

    def _set_last(self, run_id: str) -> None:
        pointer = self.root / "last"
        tmp = self.root / "last.tmp"
        tmp.write_text(run_id + "\n", encoding="utf-8")
        tmp.replace(pointer)


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total
