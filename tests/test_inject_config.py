"""Tests for diagnostic-flag injection and parse-recovery policy."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.cli import main
from diagrun.config import DiagrunConfig
from diagrun.exec.inject import DIAGNOSTIC_FLAGS, apply_inject, is_compiler_name
from diagrun.exec.runner import run_command
from diagrun.reducer.policy import retain_uncertain_dependents
from diagrun.store.runs import RunStore


class ConfigTests(unittest.TestCase):
    def test_defaults_inject_on_collapse_off(self) -> None:
        config = DiagrunConfig.resolve(env={})
        self.assertTrue(config.inject_diagnostics)
        self.assertFalse(config.collapse_parse_recovery)
        self.assertTrue(retain_uncertain_dependents(config))

    def test_env_overrides(self) -> None:
        config = DiagrunConfig.resolve(
            env={
                "DIAGRUN_INJECT_DIAGNOSTICS": "0",
                "DIAGRUN_COLLAPSE_PARSE_RECOVERY": "1",
            }
        )
        self.assertFalse(config.inject_diagnostics)
        self.assertTrue(config.collapse_parse_recovery)
        self.assertFalse(retain_uncertain_dependents(config))

    def test_cli_wins_over_env(self) -> None:
        config = DiagrunConfig.resolve(
            inject_diagnostics=True,
            collapse_parse_recovery=False,
            env={
                "DIAGRUN_INJECT_DIAGNOSTICS": "0",
                "DIAGRUN_COLLAPSE_PARSE_RECOVERY": "1",
            },
        )
        self.assertTrue(config.inject_diagnostics)
        self.assertFalse(config.collapse_parse_recovery)


class InjectApplyTests(unittest.TestCase):
    def test_compiler_argv_gets_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = apply_inject(
                ["g++", "-c", "a.cpp"],
                {"PATH": os.environ.get("PATH", "")},
                enabled=True,
                wrap_dir=Path(tmp),
            )
        self.assertEqual(result.argv[0], "g++")
        self.assertEqual(result.argv[1 : 1 + len(DIAGNOSTIC_FLAGS)], list(DIAGNOSTIC_FLAGS))
        self.assertIn("-c", result.argv)
        self.assertIn("argv.compiler", result.methods)

    def test_disabled_is_noop(self) -> None:
        argv = ["g++", "-c", "a.cpp"]
        result = apply_inject(argv, {}, enabled=False)
        self.assertEqual(result.argv, argv)
        self.assertFalse(result.enabled)
        self.assertEqual(result.flags, ())

    def test_make_wraps_cc_cxx_not_cxxflags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrap = Path(tmp)
            result = apply_inject(
                ["make", "-j2"],
                {"PATH": os.environ.get("PATH", ""), "CXXFLAGS": "-O2"},
                enabled=True,
                wrap_dir=wrap,
            )
            self.assertEqual(result.argv, ["make", "-j2"])
            self.assertEqual(result.env["CXXFLAGS"], "-O2")
            self.assertEqual(result.env["CXX"], str(wrap / "g++"))
            self.assertEqual(result.env["CC"], str(wrap / "gcc"))
            self.assertTrue((wrap / "g++").is_file())

    def test_cmake_configure_gets_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrap = Path(tmp)
            result = apply_inject(
                ["cmake", "-S", ".", "-B", "build"],
                {"PATH": os.environ.get("PATH", "")},
                wrap_dir=wrap,
            )
        self.assertTrue(any("CMAKE_CXX_COMPILER_LAUNCHER" in item for item in result.argv))
        self.assertIn("cmake.compiler_launcher", result.methods)

    def test_cmake_build_does_not_add_d_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = apply_inject(
                ["cmake", "--build", "build"],
                {"PATH": os.environ.get("PATH", "")},
                wrap_dir=Path(tmp),
            )
        self.assertEqual(result.argv, ["cmake", "--build", "build"])
        self.assertIn("cmake.build", result.methods)

    def test_ninja_gets_path_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrap = Path(tmp)
            result = apply_inject(
                ["ninja", "-C", "build"],
                {"PATH": "/usr/bin"},
                wrap_dir=wrap,
            )
            self.assertTrue(result.env["PATH"].startswith(str(wrap)))
            self.assertIn("ninja", result.methods)

    def test_clang_override_appended(self) -> None:
        result = apply_inject(
            ["make"],
            {"PATH": os.environ.get("PATH", ""), "CCC_OVERRIDE_OPTIONS": "+-g"},
            wrap_dir=Path(tempfile.mkdtemp()),
        )
        self.assertIn("+-g", result.env["CCC_OVERRIDE_OPTIONS"])
        self.assertIn("+-fdiagnostics-format=json", result.env["CCC_OVERRIDE_OPTIONS"])

    def test_compiler_name_detection(self) -> None:
        self.assertTrue(is_compiler_name("g++"))
        self.assertTrue(is_compiler_name("aarch64-linux-gnu-g++"))
        self.assertTrue(is_compiler_name("clang++-14"))
        self.assertFalse(is_compiler_name("make"))
        self.assertFalse(is_compiler_name("cmake"))


class InjectIntegrationTests(unittest.TestCase):
    def test_make_uses_wrapped_cxx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text(
                ".PHONY: all\nall:\n\t@printf '%s\\n' '$(CXX)'\n",
                encoding="utf-8",
            )
            store = RunStore(root / "store", max_runs=5, max_bytes=1_000_000)
            captured = run_command(
                ["make"],
                store,
                cwd=str(root),
                passthrough=False,
                config=DiagrunConfig(inject_diagnostics=True),
            )
            self.assertEqual(captured.exit_code, 0)
            cxx = store.stream_bytes(captured.id, "stdout").decode("utf-8").strip()
            self.assertTrue(cxx.endswith("wrappers/g++"), cxx)
            self.assertTrue(captured.inject_enabled)

    def test_no_inject_leaves_make_cxx_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text(
                ".PHONY: all\nall:\n\t@printf '%s\\n' '$(CXX)'\n",
                encoding="utf-8",
            )
            store = RunStore(root / "store", max_runs=5, max_bytes=1_000_000)
            captured = run_command(
                ["make"],
                store,
                cwd=str(root),
                passthrough=False,
                config=DiagrunConfig(inject_diagnostics=False),
            )
            cxx = store.stream_bytes(captured.id, "stdout").decode("utf-8").strip()
            self.assertEqual(cxx, "g++")
            self.assertFalse(captured.inject_enabled)

    def test_cli_no_inject_and_collapse_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "--store",
                    tmp,
                    "--no-inject-diagnostics",
                    "--collapse-parse-recovery",
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                ]
            )
            self.assertEqual(code, 0)
            store = RunStore(Path(tmp))
            meta = store.load_meta(store.last_id() or "")
            self.assertFalse(meta["inject"]["enabled"])
            self.assertTrue(meta["reducer"]["collapse_parse_recovery"])


if __name__ == "__main__":
    unittest.main()
