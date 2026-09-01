"""Tests for DSH bash-wrap command classification."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAP = ROOT / "integrations" / "dsh-diagrun" / "wrap.mjs"
NODE = Path.home() / ".local" / "node" / "bin" / "node"


CASES = [
    ("make", True),
    ("make -j8", True),
    ("make -j all", True),
    ("make -j test", False),
    ("make -C /tmp/foo", True),
    ("CXX=g++ make", True),
    ("make 2>&1", True),
    ("make test", False),
    ("make check", False),
    ("make clean", False),
    ("make && ls", False),
    ("make; ls", False),
    ("make | cat", False),
    ("make > log", False),
    ("ninja", True),
    ("ninja -t graph", False),
    ("cmake --build build", True),
    ("cmake -S . -B build", False),
    ("g++ -c a.cpp", True),
    ("/usr/bin/g++ a.cpp", True),
    ("aarch64-linux-gnu-g++ a.cpp", True),
    ("clang++ -c a.cpp", True),
    ("gcc -c a.c", False),
    ("ls", False),
    ("echo make", False),
    ("", False),
]


def _node() -> str:
    if NODE.is_file():
        return str(NODE)
    found = shutil.which("node")
    if not found:
        raise unittest.SkipTest("node not on PATH")
    return found


class BashWrapClassifyTests(unittest.TestCase):
    def test_is_build_command(self) -> None:
        script = "\n".join(
            [
                f"import {{ isBuildCommand }} from {json.dumps(WRAP.as_uri())}",
                f"const cases = {json.dumps(CASES)}",
                "let fail = 0",
                "for (const [command, expected] of cases) {",
                "  const got = isBuildCommand(command)",
                "  if (got !== expected) {",
                "    console.error(JSON.stringify({command, got, expected}))",
                "    fail += 1",
                "  }",
                "}",
                "process.exit(fail ? 1 : 0)",
            ]
        )
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as tmp:
            tmp.write(script)
            path = tmp.name
        try:
            proc = subprocess.run(
                [_node(), path],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "PATH": str(NODE.parent) + ":" + os.environ.get("PATH", "")},
            )
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_plugin_is_wrap_not_named_tools(self) -> None:
        index = (ROOT / "integrations" / "dsh-diagrun" / "index.js").read_text(encoding="utf-8")
        self.assertIn("tools/execute", index)
        self.assertIn("isBuildCommand", index)
        self.assertNotIn("defineTool", index)
        self.assertNotIn("diagrun_build", index)
        patch = (ROOT / "integrations" / "dsh-diagrun" / "cordis.patch.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("systemPrompt", patch)
        pkg = json.loads((ROOT / "integrations" / "dsh-diagrun" / "package.json").read_text())
        self.assertIn("wrap.mjs", pkg["files"])


class CompactDetectTests(unittest.TestCase):
    def test_bash_result_with_exit_marker(self) -> None:
        sys.path.insert(0, str(ROOT / "experiments" / "ab300"))
        from measure_session import _parse_json_blob, is_compact_payload

        text = '{"status":"failed","exit_code":2,"run_id":"01J","roots":[]}\n[exit code: 2]'
        payload = _parse_json_blob(text)
        self.assertTrue(is_compact_payload(payload))
        self.assertFalse(is_compact_payload({"stdout": "error: foo"}))


if __name__ == "__main__":
    unittest.main()
