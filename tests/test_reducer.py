"""Fingerprinting, grouping, and parse-recovery collapse."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.diagnostics.model import Diagnostic, DiagnosticKind, Location, Role, Severity
from diagrun.reducer.pipeline import group_diagnostics, reduce_log

SHARED = """\
g++ -c src/foo.cpp -o foo.o
[{"kind": "error", "children": [], "locations": [{"caret": {"line": 3, "file": "src/foo.cpp", "column": 32}}], "message": "‘struct Widget’ has no member named ‘foo’"}]
make: *** [Makefile:8: foo.o] Error 1
g++ -c src/bar.cpp -o bar.o
[{"kind": "error", "children": [], "locations": [{"caret": {"line": 3, "file": "src/bar.cpp", "column": 32}}], "message": "‘struct Widget’ has no member named ‘foo’"}]
make: *** [Makefile:11: bar.o] Error 1
g++ -c src/baz.cpp -o baz.o
[{"kind": "error", "children": [], "locations": [{"caret": {"line": 3, "file": "src/baz.cpp", "column": 32}}], "message": "‘struct Widget’ has no member named ‘foo’"}]
make: *** [Makefile:14: baz.o] Error 1
"""

CASCADE = """\
[{"kind": "error", "children": [], "locations": [{"caret": {"line": 2, "file": "src/main.cpp", "column": 13}}], "message": "expected primary-expression before ‘;’ token"}, {"kind": "error", "children": [], "locations": [{"caret": {"line": 3, "file": "src/main.cpp", "column": 14}}], "message": "request for member ‘missing’ in ‘x’, which is of non-class type ‘int’"}]
make: *** [Makefile:6: all] Error 1
"""


class GroupingTests(unittest.TestCase):
    def test_same_symbol_collapses_tus(self) -> None:
        result = reduce_log(
            SHARED,
            command="make",
            exit_code=2,
            run_id="01TESTGROUP00000000000000",
            raw_bytes=len(SHARED),
        )
        self.assertEqual(len(result.roots), 1)
        root = result.roots[0]
        self.assertEqual(root.representative.kind, DiagnosticKind.MISSING_MEMBER)
        self.assertEqual(root.affected_translation_units, 3)
        self.assertEqual(root.collapsed_diagnostics, 2)
        self.assertGreaterEqual(result.suppressed.build_system_messages, 3)

    def test_fingerprint_uses_symbol(self) -> None:
        a = Diagnostic(
            id="D1",
            severity=Severity.ERROR,
            message="no member named 'foo'",
            kind=DiagnosticKind.MISSING_MEMBER,
            location=Location(file="a.cpp", line=1, column=1),
            symbol="Widget::foo",
        )
        b = Diagnostic(
            id="D2",
            severity=Severity.ERROR,
            message="no member named 'foo'",
            kind=DiagnosticKind.MISSING_MEMBER,
            location=Location(file="b.cpp", line=9, column=2),
            symbol="Widget::foo",
        )
        groups = group_diagnostics([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].affected_translation_units, 2)


class ParseRecoveryTests(unittest.TestCase):
    def test_default_keeps_follow_on(self) -> None:
        result = reduce_log(
            CASCADE,
            command="make",
            exit_code=2,
            run_id="01TESTCASCADE000000000000",
            raw_bytes=len(CASCADE),
            collapse_parse_recovery=False,
        )
        self.assertGreaterEqual(len(result.roots), 2)

    def test_opt_in_collapses_follow_on(self) -> None:
        result = reduce_log(
            CASCADE,
            command="make",
            exit_code=2,
            run_id="01TESTCASCADE000000000001",
            raw_bytes=len(CASCADE),
            collapse_parse_recovery=True,
        )
        self.assertEqual(len(result.roots), 1)
        self.assertEqual(result.roots[0].representative.kind, DiagnosticKind.SYNTAX_CASCADE)
        self.assertTrue(result.unclassified)
        self.assertEqual(result.unclassified[0].role, Role.DEPENDENT)


if __name__ == "__main__":
    unittest.main()
