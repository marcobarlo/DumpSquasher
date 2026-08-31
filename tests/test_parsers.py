"""GCC/Clang parsers and Make/Ninja suppression."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.diagnostics.buildsys import count_build_system_lines
from diagrun.diagnostics.clang import parse_clang_log
from diagrun.diagnostics.gcc import parse_gcc_log
from diagrun.diagnostics.model import DiagnosticKind, Severity
from diagrun.diagnostics.textutil import extract_symbol, normalize_message


GCC_JSON = """\
g++ -c src/main.cpp -o main.o
[{"kind": "error", "children": [], "locations": [{"caret": {"line": 5, "file": "src/main.cpp", "column": 14}}], "message": "‘struct Widget’ has no member named ‘foo’"}]
make: *** [Makefile:6: all] Error 1
"""

GCC_NOTE = """\
[{"kind": "error", "children": [], "locations": [{"caret": {"line": 3, "file": "src/main.cpp", "column": 26}}], "message": "too few arguments to function ‘int add(int, int)’"}, {"kind": "note", "children": [], "locations": [{"caret": {"line": 1, "file": "src/main.cpp", "column": 5}}], "message": "declared here"}]
"""

CLANG_TEXT = """\
src/main.cpp:5:14: error: no member named 'foo' in 'Widget'
src/main.cpp:5:12: note: 'bar' declared here
"""

LINKER = """\
g++ main.o -o main
/usr/bin/ld: main.o: in function `main':
main.cpp:(.text+0x8): undefined reference to `never_defined()'
collect2: error: ld returned 1 exit status
make: *** [Makefile:7: all] Error 1
"""


class NormalizeTests(unittest.TestCase):
    def test_quotes_and_symbol(self) -> None:
        msg = normalize_message("‘struct Widget’ has no member named ‘foo’")
        self.assertIn("struct Widget", msg)
        self.assertEqual(extract_symbol(msg), "Widget::foo")


class GccJsonTests(unittest.TestCase):
    def test_missing_member_and_make_suppressed(self) -> None:
        diags = parse_gcc_log(GCC_JSON)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].kind, DiagnosticKind.MISSING_MEMBER)
        self.assertEqual(diags[0].location.file, "src/main.cpp")
        self.assertEqual(diags[0].location.line, 5)
        self.assertEqual(diags[0].symbol, "Widget::foo")
        self.assertEqual(count_build_system_lines(GCC_JSON), 1)

    def test_note_attaches_to_parent(self) -> None:
        diags = parse_gcc_log(GCC_NOTE)
        self.assertEqual(len(diags), 1)
        self.assertEqual(len(diags[0].notes), 1)
        self.assertEqual(diags[0].notes[0].message, "declared here")
        self.assertEqual(diags[0].kind, DiagnosticKind.WRONG_FUNCTION_SIGNATURE)


class ClangTextTests(unittest.TestCase):
    def test_text_fallback(self) -> None:
        diags = parse_clang_log(CLANG_TEXT)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].severity, Severity.ERROR)
        self.assertEqual(diags[0].kind, DiagnosticKind.MISSING_MEMBER)
        self.assertEqual(len(diags[0].notes), 1)


class LinkerTests(unittest.TestCase):
    def test_undefined_reference(self) -> None:
        diags = parse_gcc_log(LINKER)
        linker = [item for item in diags if item.kind == DiagnosticKind.LINKER_UNDEFINED_SYMBOL]
        self.assertEqual(len(linker), 1)
        self.assertIn("never_defined", linker[0].symbol or "")
        self.assertGreaterEqual(count_build_system_lines(LINKER), 2)


if __name__ == "__main__":
    unittest.main()
