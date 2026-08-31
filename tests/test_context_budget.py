"""Agent JSON must shrink context: roots in-band, no pretty-print, no get_raw bait."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.integrations.api import dispatch
from diagrun.render.json import dumps_compact

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "cpp_failures"
BLOATED_KEYS = ("inject", "reducer", "argv", "cwd", "diagnostics", "hint")


@unittest.skipUnless(shutil.which("g++") and shutil.which("make"), "g++/make required")
class ContextBudgetTests(unittest.TestCase):
    def _build(self, name: str) -> tuple[dict, int]:
        fixture = FIXTURES / name
        spec = json.loads((fixture / "fixture.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            result = dispatch(
                "build",
                {"command": spec["build"], "cwd": str(fixture), "store": tmp},
            )
            raw = dispatch("get_raw", {"run_id": result["run_id"], "store": tmp})
            return result, int(raw["total_bytes"])

    def test_missing_member_roots_without_hint(self) -> None:
        result, raw_bytes = self._build("missing_member")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["roots"])
        root = result["roots"][0]
        self.assertEqual(root["kind"], "missing_member")
        self.assertIn("foo", root["message"])
        self.assertIn("location", root)
        for key in BLOATED_KEYS:
            self.assertNotIn(key, result)
        encoded = dumps_compact(result)
        self.assertNotIn("\n  ", encoded)
        self.assertGreater(raw_bytes, 0)

    def test_shared_header_compact_smaller_than_raw(self) -> None:
        result, raw_bytes = self._build("shared_header_many_tus")
        self.assertEqual(len(result["roots"]), 1)
        self.assertEqual(result["roots"][0]["affected_translation_units"], 3)
        encoded = dumps_compact(result)
        self.assertLess(len(encoded.encode("utf-8")), raw_bytes)
        self.assertGreaterEqual(raw_bytes, 512)
        pretty = json.dumps(result, indent=2)
        self.assertLess(len(encoded), len(pretty))

    def test_success_has_no_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = dispatch(
                "build",
                {
                    "command": [sys.executable, "-c", "print(0)"],
                    "store": tmp,
                    "inject_diagnostics": False,
                },
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result.get("roots") or [], [])
            self.assertNotIn("hint", result)
            self.assertNotIn("inject", result)


if __name__ == "__main__":
    unittest.main()
