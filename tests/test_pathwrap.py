"""Tests for --format json and cmake PATH shims used by pip/setup.py builds."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.exec.pathwrap import install_wrappers, is_cmake_build_argv
from diagrun.exec.runner import run_command
from diagrun.store.runs import RunStore

ROOT = Path(__file__).resolve().parents[1]


class FormatJsonTests(unittest.TestCase):
    def test_json_format_hides_dump_keeps_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "diagrun",
                    "--store",
                    tmp,
                    "--format",
                    "json",
                    "--no-inject-diagnostics",
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('x' * 4000); sys.exit(2)",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn("x" * 40, proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["exit_code"], 2)
            self.assertIn("run_id", payload)


class PathWrapTests(unittest.TestCase):
    def test_is_cmake_build_argv(self) -> None:
        self.assertTrue(is_cmake_build_argv(["--build", "build"]))
        self.assertFalse(is_cmake_build_argv(["-S", ".", "-B", "build"]))
        self.assertFalse(is_cmake_build_argv(["--install", "build"]))

    def test_shim_wraps_build_not_configure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "realbin"
            fake_bin.mkdir()
            fake_cmake = fake_bin / "cmake"
            fake_cmake.write_text(
                "#!/bin/sh\n"
                "printf 'REAL_CMAKE'; printf ' %s' \"$@\"; printf '\\n'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_cmake.chmod(fake_cmake.stat().st_mode | stat.S_IEXEC)
            wrap = root / "wrap"
            written = install_wrappers(wrap, search_path=str(fake_bin))
            self.assertEqual(written, [wrap / "cmake"])
            env = {
                **os.environ,
                "PATH": str(wrap) + os.pathsep + os.environ.get("PATH", ""),
                "PYTHONPATH": str(ROOT / "src"),
                "DIAGRUN_STORE": str(root / "store"),
                "DIAGRUN_INJECT_DIAGNOSTICS": "0",
            }
            configure = subprocess.run(
                [str(wrap / "cmake"), "-S", ".", "-B", "build"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(configure.returncode, 0, configure.stderr)
            self.assertIn("REAL_CMAKE -S . -B build", configure.stdout)

            built = subprocess.run(
                [str(wrap / "cmake"), "--build", "build", "-j", "--verbose"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            self.assertNotIn("REAL_CMAKE", built.stdout)
            payload = json.loads(built.stdout)
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["exit_code"], 0)
            self.assertIn("--build", payload["command"])

    def test_active_env_skips_wrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp) / "store", max_runs=5, max_bytes=1_000_000)
            captured = run_command(
                [sys.executable, "-c", "import os; print(os.environ.get('DIAGRUN_ACTIVE', ''))"],
                store,
                passthrough=False,
            )
            self.assertEqual(captured.exit_code, 0)
            out = store.stream_bytes(captured.id, "stdout").decode("utf-8").strip()
            self.assertEqual(out, "1")


if __name__ == "__main__":
    unittest.main()
