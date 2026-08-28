"""Capture expected raw logs for C++ fixtures by running each build through diagrun."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from diagrun.config import DiagrunConfig
from diagrun.exec.runner import run_command
from diagrun.store.runs import RunStore

FIXTURES = ROOT / "fixtures" / "cpp_failures"


def capture(store_root: Path) -> None:
    store = RunStore(store_root, max_runs=50, max_bytes=50_000_000)
    for fixture_dir in sorted(path for path in FIXTURES.iterdir() if path.is_dir()):
        spec = json.loads((fixture_dir / "fixture.json").read_text(encoding="utf-8"))
        captured = run_command(
            spec["build"],
            store,
            cwd=str(fixture_dir),
            passthrough=False,
            config=DiagrunConfig(inject_diagnostics=False),
        )
        expected = fixture_dir / "expected"
        expected.mkdir(exist_ok=True)
        (expected / "raw.txt").write_bytes(store.raw_bytes(captured.id))
        print(f"{spec['name']}: exit={captured.exit_code} bytes={captured.stdout_bytes + captured.stderr_bytes}")
        if captured.exit_code == 0:
            raise SystemExit(f"fixture {spec['name']} unexpectedly succeeded")
        for leftover in fixture_dir.glob("*.o"):
            leftover.unlink()
        leftover_bin = fixture_dir / "main"
        if leftover_bin.exists():
            leftover_bin.unlink()


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        capture(Path(tmp))
