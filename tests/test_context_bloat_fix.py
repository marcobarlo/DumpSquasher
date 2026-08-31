"""Regression tests for the 2026-08-28 session-bloat root cause (plan.md §1.2).

The compact-JSON work (tasks 5-12) made every payload small, yet a DSH A/B
run still needed ~4.5x more tool calls with the tool on than off. The cause
was information-poor roots causing retries, not payload size. These tests
lock in the three concrete fixes: source snippets, classification coverage
for self-inflicted edit errors, and no-progress detection.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.diagnostics.model import DiagnosticKind, Location
from diagrun.diagnostics.snippet import read_snippet
from diagrun.diagnostics.textutil import classify_kind, extract_symbol
from diagrun.integrations.api import dispatch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "cpp_failures"

CASCADE_LOG = (
    "[{\"kind\": \"error\", \"children\": [], "
    "\"locations\": [{\"caret\": {\"line\": 6, \"file\": \"widget.hpp\", \"column\": 1}}], "
    "\"message\": \"expected declaration before '}' token\"}, "
    "{\"kind\": \"error\", \"children\": [], "
    "\"locations\": [{\"caret\": {\"line\": 8, \"file\": \"widget.hpp\", \"column\": 1}}], "
    "\"message\": \"expected unqualified-id before '}' token\"}]\n"
    "make: *** [Makefile:6: all] Error 1\n"
)


class ClassificationGapTests(unittest.TestCase):
    """These specific messages previously fell through to kind unknown."""

    def test_redeclaration_is_classified_not_unknown(self) -> None:
        message = "redeclaration of 'int Widget::bar'"
        self.assertEqual(classify_kind(message), DiagnosticKind.REDECLARATION)
        self.assertNotEqual(classify_kind(message), DiagnosticKind.UNKNOWN)
        self.assertEqual(extract_symbol(message), "Widget::bar")

    def test_invalid_conversion_is_wrong_signature_not_unknown(self) -> None:
        message = "invalid conversion from 'int (*)()' to 'int'"
        self.assertEqual(classify_kind(message), DiagnosticKind.WRONG_FUNCTION_SIGNATURE)
        self.assertNotEqual(classify_kind(message), DiagnosticKind.UNKNOWN)


class SnippetTests(unittest.TestCase):
    def test_reads_bounded_context_around_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "widget.hpp"
            path.write_text(
                "struct Widget {\n    int foo;\n    int bar;\n};\n    int bar;\n};\n"
            )
            snippet = read_snippet(tmp, Location(file="widget.hpp", line=5, column=1))
        self.assertIsNotNone(snippet)
        self.assertIn("bar", snippet)
        self.assertLessEqual(len(snippet), 160)

    def test_missing_file_degrades_to_none_not_raise(self) -> None:
        self.assertIsNone(read_snippet("/nonexistent-diagrun-test", Location(file="nope.cpp", line=1)))

    def test_no_cwd_degrades_to_none(self) -> None:
        self.assertIsNone(read_snippet(None, Location(file="relative.cpp", line=1)))


@unittest.skipUnless(shutil.which("g++") and shutil.which("make"), "g++/make required")
class NoProgressTests(unittest.TestCase):
    def test_repeated_build_without_edit_flags_no_progress(self) -> None:
        fixture = FIXTURES / "missing_member"
        with tempfile.TemporaryDirectory() as tmp:
            first = dispatch("build", {"command": "make", "cwd": str(fixture), "store": tmp})
            second = dispatch("build", {"command": "make", "cwd": str(fixture), "store": tmp})
        self.assertEqual(first["status"], "failed")
        self.assertNotIn("no_progress", first)
        self.assertEqual(second["status"], "failed")
        self.assertEqual(first["roots"][0]["kind"], second["roots"][0]["kind"])
        self.assertTrue(second.get("no_progress"))
        self.assertIn("hint", second)


class CollapseParseRecoveryDefaultTests(unittest.TestCase):
    def test_agent_build_defaults_to_collapsing_cascades(self) -> None:
        script = f"import sys; sys.stdout.write({CASCADE_LOG!r}); sys.exit(2)"
        with tempfile.TemporaryDirectory() as tmp:
            result = dispatch(
                "build",
                {"command": [sys.executable, "-c", script], "store": tmp},
            )
        # Only the causing syntax_cascade root remains; the wrong_function_signature
        # follow-on must be folded into its evidence, not listed as a second root.
        self.assertEqual(len(result["roots"]), 1)
        root = result["roots"][0]
        self.assertEqual(root["kind"], DiagnosticKind.SYNTAX_CASCADE)
        self.assertTrue(any("parse-recovery" in item for item in root.get("evidence", [])))

    def test_explicit_false_keeps_both_roots(self) -> None:
        script = f"import sys; sys.stdout.write({CASCADE_LOG!r}); sys.exit(2)"
        with tempfile.TemporaryDirectory() as tmp:
            result = dispatch(
                "build",
                {
                    "command": [sys.executable, "-c", script],
                    "store": tmp,
                    "collapse_parse_recovery": False,
                },
            )
        self.assertGreaterEqual(len(result["roots"]), 2)


if __name__ == "__main__":
    unittest.main()
