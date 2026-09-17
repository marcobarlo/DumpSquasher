"""Always-on compiler dump vs agent-summary instrumentation."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.cli import main
from diagrun.integrations.api import dispatch
from diagrun.instrument import record_build


class InstrumentTests(unittest.TestCase):
    def test_tool_build_logs_compiler_and_agent_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dump = "E" * 4000
            built = dispatch(
                "build",
                {
                    "command": [
                        sys.executable,
                        "-c",
                        "import sys; sys.stderr.write('E' * 4000); sys.exit(2)",
                    ],
                    "store": tmp,
                    "inject_diagnostics": False,
                },
            )
            run_id = built["run_id"]
            inst = Path(tmp) / "instrument" / run_id
            compiler = (inst / "compiler.log").read_text(encoding="utf-8")
            agent = json.loads((inst / "agent.json").read_text(encoding="utf-8"))
            self.assertIn(dump, compiler)
            self.assertEqual(agent["run_id"], run_id)
            self.assertEqual(agent["exit_code"], 2)
            self.assertNotIn(dump, (inst / "agent.json").read_text(encoding="utf-8"))
            journal = (Path(tmp) / "instrument" / "journal.jsonl").read_text(encoding="utf-8")
            row = json.loads(journal.splitlines()[-1])
            self.assertTrue(row["returned_to_agent"])
            self.assertEqual(row["surface"], "tool_build")
            self.assertGreater(row["compiler_bytes"], row["agent_bytes"])
            log = (Path(tmp) / "instrument" / "diagrun-instrument.log").read_text(encoding="utf-8")
            self.assertIn(run_id, log)

    def test_cli_json_does_not_leak_instrument_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                code = main(
                    [
                        "--store",
                        tmp,
                        "--format",
                        "json",
                        "--no-inject-diagnostics",
                        "--",
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.write('DUMPDATA'); sys.exit(2)",
                    ]
                )
            finally:
                sys.stdout = old
            self.assertEqual(code, 2)
            stdout = buf.getvalue()
            self.assertNotIn("compiler.log", stdout)
            self.assertNotIn("diagrun-instrument", stdout)
            payload = json.loads(stdout)
            inst = Path(tmp) / "instrument"
            row = json.loads((inst / "journal.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            compiler = (inst / row["run_id"] / "compiler.log").read_text(encoding="utf-8")
            self.assertIn("DUMPDATA", compiler)
            self.assertEqual(payload["run_id"], row["run_id"])
            self.assertEqual(row["surface"], "cli_json")
            self.assertTrue(row["returned_to_agent"])

    def test_disable_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("DIAGRUN_INSTRUMENT")
            os.environ["DIAGRUN_INSTRUMENT"] = "0"
            try:
                dispatch(
                    "build",
                    {
                        "command": [sys.executable, "-c", "print(1)"],
                        "store": tmp,
                        "inject_diagnostics": False,
                    },
                )
            finally:
                if old is None:
                    os.environ.pop("DIAGRUN_INSTRUMENT", None)
                else:
                    os.environ["DIAGRUN_INSTRUMENT"] = old
            self.assertFalse((Path(tmp) / "instrument").exists())

    def test_record_failure_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from diagrun.exec.runner import CapturedRun
            from diagrun.store.runs import RunStore

            store = RunStore(Path(tmp))
            captured = CapturedRun(
                id="01NOTREAL000000000000000000",
                command=("false",),
                cwd=tmp,
                exit_code=1,
                started_at_ns=0,
                finished_at_ns=1,
                stdout_bytes=0,
                stderr_bytes=0,
                event_count=0,
            )
            with mock.patch.object(store, "raw_bytes", side_effect=OSError("boom")):
                self.assertIsNone(
                    record_build(
                        store,
                        captured,
                        {"status": "failed", "roots": []},
                        surface="tool_build",
                        returned_to_agent=True,
                    )
                )

    def test_instrument_dir_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store_dir = Path(tmp) / "store"
            inst_dir = Path(tmp) / "elsewhere"
            built = dispatch(
                "build",
                {
                    "command": [sys.executable, "-c", "print('ok')"],
                    "store": str(store_dir),
                    "inject_diagnostics": False,
                    "env": {**os.environ, "DIAGRUN_INSTRUMENT_DIR": str(inst_dir)},
                },
            )
            self.assertTrue((inst_dir / built["run_id"] / "agent.json").is_file())
            self.assertTrue((inst_dir / built["run_id"] / "compiler.log").is_file())
            self.assertFalse((store_dir / "instrument").exists())


if __name__ == "__main__":
    unittest.main()
