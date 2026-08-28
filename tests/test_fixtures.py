"""Fixture corpus checks (plan.md §18, task 4)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.diagnostics.model import Diagnostic, DiagnosticGroup
from diagrun.exec.runner import run_command
from diagrun.store.runs import RunStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cpp_failures"
REQUIRED = (
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
EXPECTED_FILES = ("raw.txt", "normalized.json", "roots.json", "collapsed.json")


def _fixture_dirs() -> list[Path]:
    return sorted(
        path
        for path in FIXTURES.iterdir()
        if path.is_dir() and (path / "fixture.json").is_file()
    )


class FixtureCorpusTests(unittest.TestCase):
    def test_ten_named_fixtures_exist(self) -> None:
        names = {path.name for path in _fixture_dirs()}
        self.assertEqual(names, set(REQUIRED))

    def test_expected_artifacts_and_schema(self) -> None:
        for fixture_dir in _fixture_dirs():
            spec = json.loads((fixture_dir / "fixture.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["name"], fixture_dir.name)
            self.assertTrue(spec["build"])
            expected = fixture_dir / "expected"
            for name in EXPECTED_FILES:
                path = expected / name
                self.assertTrue(path.is_file(), f"missing {path}")
                self.assertGreater(path.stat().st_size, 0)
            raw = (expected / "raw.txt").read_text(encoding="utf-8", errors="replace")
            self.assertTrue(raw.strip())
            normalized = json.loads((expected / "normalized.json").read_text(encoding="utf-8"))
            roots = json.loads((expected / "roots.json").read_text(encoding="utf-8"))
            collapsed = json.loads((expected / "collapsed.json").read_text(encoding="utf-8"))
            self.assertIsInstance(normalized, list)
            self.assertGreaterEqual(len(normalized), 1)
            self.assertIsInstance(roots, list)
            self.assertGreaterEqual(len(roots), 1)
            self.assertIn("build_system_messages", collapsed)
            for item in normalized:
                Diagnostic.from_dict(item)
            for item in roots:
                DiagnosticGroup.from_dict(item)

    @unittest.skipUnless(shutil.which("g++") and shutil.which("make"), "g++/make required")
    def test_each_fixture_build_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp), max_runs=20, max_bytes=10_000_000)
            for fixture_dir in _fixture_dirs():
                spec = json.loads((fixture_dir / "fixture.json").read_text(encoding="utf-8"))
                captured = run_command(
                    spec["build"],
                    store,
                    cwd=str(fixture_dir),
                    passthrough=False,
                )
                self.assertNotEqual(
                    captured.exit_code,
                    0,
                    f"{spec['name']} unexpectedly succeeded",
                )
                self.assertGreater(captured.stderr_bytes + captured.stdout_bytes, 0)


if __name__ == "__main__":
    unittest.main()
