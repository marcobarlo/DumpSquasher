"""Tests for agent tool API, MCP server, and plugin manifests."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun.cli import main
from diagrun.integrations.api import dispatch, parse_command
from diagrun.integrations.mcp_server import TOOLS, _handle_request, _write_message

ROOT = Path(__file__).resolve().parents[1]


def _mcp(method: str, params: object = None, req_id: int = 1) -> dict:
    message = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        message["params"] = params
    return _handle_request(message)


class ParseCommandTests(unittest.TestCase):
    def test_string_and_list(self) -> None:
        self.assertEqual(parse_command("cmake --build build"), ["cmake", "--build", "build"])
        self.assertEqual(parse_command(["make", "-j2"]), ["make", "-j2"])

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            parse_command("")
        with self.assertRaises(ValueError):
            parse_command([])


class ToolApiTests(unittest.TestCase):
    def test_build_show_raw_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            built = dispatch(
                "build",
                {
                    "command": [sys.executable, "-c", "import sys; sys.stdout.write('out'); sys.stderr.write('err')"],
                    "store": tmp,
                    "inject_diagnostics": False,
                },
            )
            self.assertEqual(built["exit_code"], 0)
            self.assertEqual(built["status"], "passed")
            self.assertIn("run_id", built)
            raw = dispatch("get_raw", {"run_id": built["run_id"], "store": tmp})
            self.assertEqual(raw["text"], "outerr")
            shown = dispatch("show", {"run_id": built["run_id"], "store": tmp})
            self.assertEqual(shown["id"], built["run_id"])
            missing = dispatch(
                "get_diagnostic",
                {"run_id": built["run_id"], "diagnostic_id": "D1", "store": tmp},
            )
            self.assertFalse(missing["available"])

    def test_call_subcommand_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = sys.stdin
            try:
                sys.stdin = io.StringIO(
                    json.dumps(
                        {
                            "command": [sys.executable, "-c", "print(1)"],
                            "store": tmp,
                            "inject_diagnostics": False,
                        }
                    )
                )
                buf = io.StringIO()
                old_out = sys.stdout
                sys.stdout = buf
                code = main(["--store", tmp, "call", "build"])
            finally:
                sys.stdin = old
                sys.stdout = old_out
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["exit_code"], 0)


class McpTests(unittest.TestCase):
    def test_initialize_and_list_tools(self) -> None:
        init = _mcp("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "diagrun")
        listed = _mcp("tools/list")
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(
            names,
            {"diagrun_build", "diagrun_get_raw", "diagrun_show", "diagrun_get_diagnostic"},
        )
        self.assertEqual(len(TOOLS), 4)

    def test_tools_call_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DIAGRUN_STORE"] = tmp
            try:
                response = _mcp(
                    "tools/call",
                    {
                        "name": "diagrun_build",
                        "arguments": {
                            "command": [sys.executable, "-c", "print('ok')"],
                            "store": tmp,
                            "inject_diagnostics": False,
                        },
                    },
                )
            finally:
                os.environ.pop("DIAGRUN_STORE", None)
            self.assertFalse(response["result"]["isError"])
            body = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(body["exit_code"], 0)

    def test_framing_roundtrip(self) -> None:
        buf = io.BytesIO()
        _write_message(buf, {"jsonrpc": "2.0", "id": 1, "result": {}})
        blob = buf.getvalue()
        self.assertTrue(blob.startswith(b"Content-Length: "))
        self.assertIn(b"\r\n\r\n", blob)


class PluginManifestTests(unittest.TestCase):
    def test_agent_plugin_manifests(self) -> None:
        plugin = json.loads((ROOT / "plugins" / "diagrun" / "plugin.json").read_text())
        mcp = json.loads((ROOT / "plugins" / "diagrun" / "mcp.json").read_text())
        self.assertEqual(plugin["name"], "diagrun")
        self.assertIn("dev.pi.agent", plugin["extensions"])
        self.assertEqual(set(mcp.keys()), {"$schema", "mcpServers"})
        self.assertEqual(mcp["mcpServers"]["diagrun"]["type"], "stdio")
        script = ROOT / "plugins" / "diagrun" / "bin" / "diagrun-mcp"
        self.assertTrue(script.is_file())
        self.assertTrue(os.stat(script).st_mode & stat.S_IEXEC)

    def test_dsh_bundle_manifest(self) -> None:
        pkg = json.loads((ROOT / "integrations" / "dsh-diagrun" / "package.json").read_text())
        self.assertEqual(pkg["dsh"]["bundle"]["patch"], "./cordis.patch.yml")
        self.assertTrue((ROOT / "integrations" / "dsh-diagrun" / "index.js").is_file())
        self.assertTrue((ROOT / "plugins" / "diagrun" / "dev.pi.agent" / "index.ts").is_file())


if __name__ == "__main__":
    unittest.main()
