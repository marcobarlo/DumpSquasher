"""Comprehensive tests for plan.md tasks 2–4: capture, store, CLI, fixtures."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun import __version__
from diagrun.cli import USAGE, main
from diagrun.exec.capture import CaptureSink, StreamEvent, load_events, reconstruct_raw
from diagrun.exec.runner import run_command
from diagrun.ids import encode_ulid, is_run_id, new_ulid
from diagrun.store.runs import RunNotFoundError, RunStore, default_store_path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp_failures"
PLAN_FIXTURES = (
    "missing_include",
    "missing_member",
    "wrong_function_signature",
    "template_instantiation",
    "concept_failure",
    "syntax_cascade",
    "shared_header_many_tus",
    "multiple_independent_errors",
    "linker_undefined_symbol",
    "make_propagation",
)


def _py(script: str) -> list[str]:
    return [sys.executable, "-c", script]


class UlidContractTests(unittest.TestCase):
    def test_encode_rejects_bad_inputs(self) -> None:
        with self.assertRaises(ValueError):
            encode_ulid(-1, b"\x00" * 10)
        with self.assertRaises(ValueError):
            encode_ulid(2**48, b"\x00" * 10)
        with self.assertRaises(ValueError):
            encode_ulid(0, b"\x00" * 9)

    def test_is_run_id_rejects_crockford_exclusions(self) -> None:
        self.assertFalse(is_run_id(""))
        self.assertFalse(is_run_id("I" * 26))
        self.assertFalse(is_run_id("L" * 26))
        self.assertFalse(is_run_id("O" * 26))
        self.assertFalse(is_run_id("U" * 26))

    def test_is_run_id_accepts_lowercase(self) -> None:
        run_id = new_ulid()
        self.assertTrue(is_run_id(run_id.lower()))

    def test_time_sortable_prefix(self) -> None:
        earlier = encode_ulid(1, b"\x00" * 10)
        later = encode_ulid(2, b"\x00" * 10)
        self.assertLess(earlier, later)


class CaptureSinkTests(unittest.TestCase):
    def test_roundtrip_events_and_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with CaptureSink(run_dir) as sink:
                sink.write("stdout", b"", t_ns=1)
                sink.write("stdout", b"AA", t_ns=2)
                sink.write("stderr", b"B", t_ns=3)
                sink.write("stdout", b"C", t_ns=4)
                self.assertEqual(sink.stdout_bytes, 3)
                self.assertEqual(sink.stderr_bytes, 1)
                self.assertEqual(sink.event_count, 3)
            events = load_events(run_dir / "events.jsonl")
            self.assertEqual([event.seq for event in events], [0, 1, 2])
            stdout = (run_dir / "stdout.bin").read_bytes()
            stderr = (run_dir / "stderr.bin").read_bytes()
            self.assertEqual(reconstruct_raw(stdout, stderr, events), b"AABC")

    def test_load_events_missing_file(self) -> None:
        self.assertEqual(load_events(Path("/no/such/events.jsonl")), [])

    def test_stream_event_rejects_unknown_stream(self) -> None:
        with self.assertRaises(ValueError):
            StreamEvent.from_dict(
                {"seq": 0, "stream": "stdin", "t_ns": 1, "offset": 0, "size": 1}
            )


class RunnerSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self._tmp.name), max_runs=50, max_bytes=10_000_000)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_command_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_command([], self.store, passthrough=False)

    def test_non_executable_is_126(self) -> None:
        script = Path(self._tmp.name) / "noexec.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(script.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
        captured = run_command([str(script)], self.store, passthrough=False)
        self.assertEqual(captured.exit_code, 126)

    def test_child_stdout_is_not_a_tty(self) -> None:
        captured = run_command(
            _py("import sys; sys.exit(0 if not sys.stdout.isatty() else 1)"),
            self.store,
            passthrough=False,
        )
        self.assertEqual(captured.exit_code, 0)

    def test_stdin_is_closed(self) -> None:
        captured = run_command(
            _py("import sys; sys.exit(0 if sys.stdin.read() == '' else 1)"),
            self.store,
            passthrough=False,
        )
        self.assertEqual(captured.exit_code, 0)

    def test_cwd_is_honored(self) -> None:
        nested = Path(self._tmp.name) / "nested"
        nested.mkdir()
        captured = run_command(
            _py("import os, sys; sys.stdout.write(os.getcwd())"),
            self.store,
            cwd=str(nested),
            passthrough=False,
        )
        self.assertEqual(self.store.stream_bytes(captured.id, "stdout"), str(nested).encode())

    def test_custom_env_replaces_parent_env(self) -> None:
        captured = run_command(
            _py(
                "import os, sys;"
                "sys.stdout.write('1' if os.environ.get('DIAGRUN_TEST_FLAG') == 'yes' else '0');"
                "sys.stdout.write('P' if 'PATH' in os.environ else 'x')"
            ),
            self.store,
            env={"DIAGRUN_TEST_FLAG": "yes"},
            passthrough=False,
        )
        payload = self.store.stream_bytes(captured.id, "stdout")
        self.assertTrue(payload.startswith(b"1"), payload)
        # Replacement (not merge) means PATH is absent unless the caller supplied it.
        self.assertEqual(payload, b"1x")

    def test_timing_and_command_tuple(self) -> None:
        argv = _py("print('x')")
        captured = run_command(argv, self.store, passthrough=False)
        self.assertGreaterEqual(captured.finished_at_ns, captured.started_at_ns)
        self.assertEqual(captured.command, tuple(argv))
        self.assertGreater(captured.stdout_bytes, 0)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_default_store_path_prefers_diagrun_store(self) -> None:
        with patch.dict(os.environ, {"DIAGRUN_STORE": "/tmp/diagrun-store-test"}, clear=False):
            self.assertEqual(default_store_path(), Path("/tmp/diagrun-store-test"))

    def test_default_store_path_uses_xdg(self) -> None:
        env = {"XDG_DATA_HOME": "/xdg", "DIAGRUN_STORE": ""}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("DIAGRUN_STORE", None)
            with patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg"}, clear=False):
                os.environ.pop("DIAGRUN_STORE", None)
                self.assertEqual(default_store_path(), Path("/xdg") / "diagrun")

    def test_run_dir_rejects_traversal(self) -> None:
        store = RunStore(self.root, max_runs=10, max_bytes=10_000_000)
        with self.assertRaises(ValueError):
            store.run_dir("../escape")
        with self.assertRaises(ValueError):
            store.run_dir("..")

    def test_gc_by_bytes_keeps_newest(self) -> None:
        store = RunStore(self.root / "bytes", max_runs=50, max_bytes=400)
        ids = []
        for _ in range(6):
            ids.append(
                run_command(
                    _py("import sys; sys.stdout.write('Z' * 80)"),
                    store,
                    passthrough=False,
                ).id
            )
        remaining = {path.name for path in store.runs_dir.iterdir() if path.is_dir()}
        self.assertTrue(remaining)
        self.assertNotIn(ids[0], remaining)
        self.assertIn(ids[-1], remaining)

    def test_gc_disabled_when_limits_non_positive(self) -> None:
        store = RunStore(self.root / "nolimit", max_runs=0, max_bytes=0)
        ids = [
            run_command(_py("print(1)"), store, passthrough=False).id,
            run_command(_py("print(2)"), store, passthrough=False).id,
            run_command(_py("print(3)"), store, passthrough=False).id,
        ]
        remaining = {path.name for path in store.runs_dir.iterdir() if path.is_dir()}
        self.assertEqual(remaining, set(ids))

    def test_last_id_none_when_empty(self) -> None:
        store = RunStore(self.root / "empty", max_runs=10, max_bytes=1000)
        self.assertIsNone(store.last_id())

    def test_load_meta_missing(self) -> None:
        store = RunStore(self.root / "empty", max_runs=10, max_bytes=1000)
        with self.assertRaises(RunNotFoundError):
            store.load_meta("0" * 26)

    def test_oversized_keep_run_is_retained(self) -> None:
        store = RunStore(self.root / "keep", max_runs=10, max_bytes=50)
        captured = run_command(
            _py("import sys; sys.stdout.write('Q' * 400)"),
            store,
            passthrough=False,
        )
        remaining = {path.name for path in store.runs_dir.iterdir() if path.is_dir()}
        self.assertEqual(remaining, {captured.id})


class CliContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_help_and_version(self) -> None:
        stdout = io.StringIO()
        with patch.object(sys, "stdout", stdout):
            self.assertEqual(main(["--help"]), 0)
            self.assertIn("diagrun", stdout.getvalue())
            self.assertIn(USAGE.splitlines()[0], stdout.getvalue())
        stdout = io.StringIO()
        with patch.object(sys, "stdout", stdout):
            self.assertEqual(main(["--version"]), 0)
            self.assertEqual(stdout.getvalue().strip(), __version__)

    def test_unknown_option_and_missing_store_arg(self) -> None:
        stderr = io.StringIO()
        with patch.object(sys, "stderr", stderr):
            self.assertEqual(main(["--nope"]), 2)
            self.assertEqual(main(["--store"]), 2)
            self.assertEqual(main([]), 2)

    def test_show_rejects_last_and_id_together(self) -> None:
        self.assertEqual(
            main(["--store", str(self.root), "show", "--last", "0" * 26]),
            2,
        )

    def test_show_last_with_no_runs(self) -> None:
        self.assertEqual(main(["--store", str(self.root), "show", "--last"]), 2)

    def test_store_equals_form_and_max_runs(self) -> None:
        code = main(
            [
                f"--store={self.root}",
                "--max-runs",
                "5",
                "--",
                sys.executable,
                "-c",
                "print('ok')",
            ]
        )
        self.assertEqual(code, 0)
        store = RunStore(self.root, max_runs=5, max_bytes=10_000_000)
        self.assertIsNotNone(store.last_id())

    def test_double_dash_allows_show_as_command(self) -> None:
        code = main(
            [
                "--store",
                str(self.root),
                "--",
                sys.executable,
                "-c",
                "import sys; sys.exit(0)",
            ]
        )
        self.assertEqual(code, 0)


class FixtureCorpusContractTests(unittest.TestCase):
    def _dirs(self) -> list[Path]:
        return sorted(
            path
            for path in FIXTURES.iterdir()
            if path.is_dir() and (path / "fixture.json").is_file()
        )

    def test_exactly_the_ten_plan_fixtures(self) -> None:
        names = {path.name for path in self._dirs()}
        self.assertEqual(names, set(PLAN_FIXTURES))

    def test_each_fixture_has_source_tree_and_build(self) -> None:
        for fixture_dir in self._dirs():
            spec = json.loads((fixture_dir / "fixture.json").read_text(encoding="utf-8"))
            self.assertTrue(spec["build"], fixture_dir.name)
            self.assertEqual(spec["build"][0], "make")
            self.assertTrue(
                (fixture_dir / "Makefile").is_file()
                or (fixture_dir / "lib" / "Makefile").is_file(),
                fixture_dir.name,
            )
            sources = list(fixture_dir.rglob("*.cpp")) + list(fixture_dir.rglob("*.hpp"))
            self.assertTrue(sources, fixture_dir.name)

    def test_shared_header_cluster_count(self) -> None:
        roots = json.loads(
            (FIXTURES / "shared_header_many_tus" / "expected" / "roots.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["affected_translation_units"], 3)

    def test_independent_errors_remain_two_roots(self) -> None:
        roots = json.loads(
            (
                FIXTURES / "multiple_independent_errors" / "expected" / "roots.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(roots), 2)
        kinds = {item["kind"] for item in roots}
        self.assertEqual(kinds, {"undeclared_identifier", "missing_member"})

    def test_make_propagation_records_build_consequences(self) -> None:
        collapsed = json.loads(
            (FIXTURES / "make_propagation" / "expected" / "collapsed.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertGreaterEqual(collapsed["build_system_messages"], 2)
        raw = (FIXTURES / "make_propagation" / "expected" / "raw.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("Error 1", raw)
        self.assertIn("Error 2", raw)

    def test_roots_have_concrete_locations(self) -> None:
        for fixture_dir in self._dirs():
            roots = json.loads((fixture_dir / "expected" / "roots.json").read_text())
            for root in roots:
                self.assertIn(
                    "location",
                    root,
                    f"{fixture_dir.name} root {root.get('id')} has no location",
                )
                loc = root["location"]
                self.assertTrue(loc["file"])
                self.assertGreaterEqual(loc["line"], 1)

    def test_expected_raw_contains_compiler_or_linker_evidence(self) -> None:
        needles = {
            "missing_include": "No such file",
            "missing_member": "has no member named",
            "wrong_function_signature": "too few arguments",
            "template_instantiation": "request for member",
            "concept_failure": "constraints not satisfied",
            "syntax_cascade": "expected",
            "shared_header_many_tus": "has no member named",
            "multiple_independent_errors": "MISSING_ONE",
            "linker_undefined_symbol": "undefined reference",
            "make_propagation": "NOT_DEFINED",
        }
        for name, needle in needles.items():
            raw = (FIXTURES / name / "expected" / "raw.txt").read_text(encoding="utf-8")
            self.assertIn(needle, raw, name)


if __name__ == "__main__":
    unittest.main()
