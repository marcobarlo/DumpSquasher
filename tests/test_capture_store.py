"""Tests for stream capture, runner, store, and CLI (plan.md §7, tasks 2–3)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.cli import main
from diagrun.exec.capture import StreamEvent, reconstruct_raw
from diagrun.exec.runner import run_command
from diagrun.ids import is_run_id
from diagrun.store.runs import RunNotFoundError, RunStore


def _py(script: str) -> list[str]:
    return [sys.executable, "-c", script]


class ReconstructTests(unittest.TestCase):
    def test_interleaves_by_event_order(self) -> None:
        stdout = b"AC"
        stderr = b"B"
        events = [
            StreamEvent(seq=0, stream="stdout", t_ns=1, offset=0, size=1),
            StreamEvent(seq=1, stream="stderr", t_ns=2, offset=0, size=1),
            StreamEvent(seq=2, stream="stdout", t_ns=3, offset=1, size=1),
        ]
        self.assertEqual(reconstruct_raw(stdout, stderr, events), b"ABC")

    def test_empty_events_concatenates_stdout_then_stderr(self) -> None:
        self.assertEqual(reconstruct_raw(b"out", b"err", []), b"outerr")


class RunnerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self._tmp.name), max_runs=20, max_bytes=10_000_000)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_captures_stdout_and_stderr_independently(self) -> None:
        captured = run_command(
            _py("import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')"),
            self.store,
            passthrough=False,
        )
        self.assertEqual(captured.exit_code, 0)
        self.assertTrue(is_run_id(captured.id))
        self.assertEqual(self.store.stream_bytes(captured.id, "stdout"), b"OUT")
        self.assertEqual(self.store.stream_bytes(captured.id, "stderr"), b"ERR")

    def test_preserves_interleaved_order(self) -> None:
        script = (
            "import sys, time;"
            "sys.stdout.write('A'); sys.stdout.flush(); time.sleep(0.05);"
            "sys.stderr.write('B'); sys.stderr.flush(); time.sleep(0.05);"
            "sys.stdout.write('C'); sys.stdout.flush()"
        )
        captured = run_command(_py(script), self.store, passthrough=False)
        self.assertEqual(self.store.raw_bytes(captured.id), b"ABC")

    def test_returns_original_exit_code(self) -> None:
        captured = run_command(_py("import sys; sys.exit(3)"), self.store, passthrough=False)
        self.assertEqual(captured.exit_code, 3)

    def test_missing_command_is_127(self) -> None:
        captured = run_command(
            ["diagrun-no-such-command-xyz"],
            self.store,
            passthrough=False,
        )
        self.assertEqual(captured.exit_code, 127)

    def test_passthrough_tees_live_bytes(self) -> None:
        out = io.BytesIO()
        err = io.BytesIO()
        run_command(
            _py("import sys; sys.stdout.write('O'); sys.stderr.write('E')"),
            self.store,
            passthrough=True,
            stdout=out,
            stderr=err,
        )
        self.assertEqual(out.getvalue(), b"O")
        self.assertEqual(err.getvalue(), b"E")

    def test_last_pointer_and_meta(self) -> None:
        captured = run_command(_py("print('hi')"), self.store, passthrough=False)
        self.assertEqual(self.store.last_id(), captured.id)
        meta = self.store.load_meta(captured.id)
        self.assertEqual(meta["exit_code"], 0)
        self.assertEqual(meta["raw"]["ref"], f"diag://run/{captured.id}/raw")

    def test_unknown_run_raises(self) -> None:
        with self.assertRaises(RunNotFoundError):
            self.store.raw_bytes("0" * 26)

    def test_gc_keeps_newest_under_max_runs(self) -> None:
        small = RunStore(Path(self._tmp.name) / "gc", max_runs=2, max_bytes=10_000_000)
        ids = [
            run_command(_py("print(1)"), small, passthrough=False).id,
            run_command(_py("print(2)"), small, passthrough=False).id,
            run_command(_py("print(3)"), small, passthrough=False).id,
        ]
        remaining = {path.name for path in (small.runs_dir).iterdir() if path.is_dir()}
        self.assertEqual(remaining, {ids[1], ids[2]})
        self.assertEqual(small.last_id(), ids[2])


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_wrap_then_show_raw(self) -> None:
        class _Sink:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()
                self.text = io.StringIO()

            def write(self, data: str) -> int:
                return self.text.write(data)

            def flush(self) -> None:
                return None

        sink = _Sink()
        old_out = sys.stdout
        try:
            sys.stdout = sink  # type: ignore[assignment]
            code = main(
                [
                    "--store",
                    str(self.root),
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('hello')",
                ]
            )
            self.assertEqual(code, 0)
            sink.text.seek(0)
            sink.text.truncate(0)
            self.assertEqual(main(["--store", str(self.root), "show", "--last"]), 0)
            meta = json.loads(sink.text.getvalue())
            run_id = meta["id"]
            sink.buffer.seek(0)
            sink.buffer.truncate(0)
            self.assertEqual(main(["--store", str(self.root), "show", run_id, "--raw"]), 0)
        finally:
            sys.stdout = old_out
        self.assertTrue(run_id)
        self.assertEqual(sink.buffer.getvalue(), b"hello")

    def test_forwards_build_flags_after_command(self) -> None:
        code = main(
            [
                "--store",
                str(self.root),
                sys.executable,
                "-c",
                "import sys; sys.exit(0 if '--build' in sys.argv else 9)",
                "--build",
            ]
        )
        self.assertEqual(code, 0)

    def test_show_missing_run(self) -> None:
        self.assertEqual(main(["--store", str(self.root), "show", "0" * 26]), 2)


if __name__ == "__main__":
    unittest.main()
