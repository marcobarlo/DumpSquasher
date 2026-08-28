"""Tests for ULID run identifiers."""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.ids import encode_ulid, is_run_id, new_ulid


class UlidTests(unittest.TestCase):
    def test_length_and_alphabet(self) -> None:
        run_id = new_ulid()
        self.assertEqual(len(run_id), 26)
        self.assertTrue(is_run_id(run_id))

    def test_monotonic_when_generated_quickly(self) -> None:
        ids = [new_ulid() for _ in range(50)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 50)

    def test_encode_known_zero(self) -> None:
        self.assertEqual(encode_ulid(0, b"\x00" * 10), "0" * 26)

    def test_rejects_short_strings(self) -> None:
        self.assertFalse(is_run_id("01JXYZ"))


if __name__ == "__main__":
    unittest.main()
